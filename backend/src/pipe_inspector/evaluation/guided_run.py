"""Bounded, resumable evaluation over previously cached image observations.

The baseline detector is never loaded here. A recorded provider failure is a
completed single attempt and resumes as raw-detector fallback. There is one
unavoidable at-least-once gap: a process crash after the provider accepts a
request but before the sample record is checkpointed can cause that sample to
be submitted again on resume. Request evidence is kept for both submissions.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.guided_review import (
    GUIDED_CLASS_PROTECTION_THRESHOLDS,
    GUIDED_HIGH_SCORE_THRESHOLD,
    GUIDED_PROMPT_VERSION,
    GUIDED_PROTECTION_POLICY_VERSION,
    SYSTEM_PROMPT,
    VIEW_POLICY_VERSION,
    GuidedImageReviewer,
    GuidedResponse,
    GuidedReview,
)
from pipe_inspector.agent.image_review import LABEL_GUIDANCE
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.evaluation.guided import (
    ADDITION_DUPLICATE_IOU,
    FUSION_VERSION,
    NOVELTY_IOU,
    fuse_guided,
)
from pipe_inspector.evaluation.review import (
    MATCH_IOU,
    EvaluationManifest,
    LabeledBox,
    _matched_pairs,
    _merge_scores,
    _raw_boxes,
    score_boxes,
)

MAX_IMAGES = 48
STATE_SCHEMA_VERSION = "guided-cached-evaluation-v1"
AT_LEAST_ONCE_NOTICE = (
    "Saved failures are never retried. A crash after a provider accepts a request but before "
    "the sample checkpoint is durable can submit that unrecorded sample again on resume."
)
_RECORD_KEYS = {
    "sample_id",
    "observation",
    "review",
    "review_error",
    "predictions",
    "changes",
    "duration_seconds",
}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_error(error: Exception) -> str:
    return error.code if isinstance(error, APIClientError) else "unexpected_error"


def _validate_nonsecret_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    safe = _json_copy(dict(settings))
    for key in safe:
        lowered = key.lower()
        if (
            "authorization" in lowered
            or "secret" in lowered
            or "api_key" in lowered
            or lowered in {"token", "access_token", "refresh_token"}
        ):
            raise ValueError("provider settings must not contain credentials")
    return safe


def _run_pins(provider_settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "guided_review": {
            "prompt_version": GUIDED_PROMPT_VERSION,
            "view_policy_version": VIEW_POLICY_VERSION,
            "system_prompt": SYSTEM_PROMPT,
            "label_guidance": _json_copy(LABEL_GUIDANCE),
            "response_schema": GuidedResponse.model_json_schema(),
        },
        "fusion": {
            "version": FUSION_VERSION,
            "high_score_threshold": GUIDED_HIGH_SCORE_THRESHOLD,
            "class_protection_thresholds": _json_copy(GUIDED_CLASS_PROTECTION_THRESHOLDS),
            "protection_policy_version": GUIDED_PROTECTION_POLICY_VERSION,
            "novelty_iou": NOVELTY_IOU,
            "addition_duplicate_iou": ADDITION_DUPLICATE_IOU,
        },
        "scoring": {"class_aware_iou": MATCH_IOU},
        "provider": _validate_nonsecret_settings(provider_settings),
        "maximum_images": MAX_IMAGES,
    }


def _load_baseline(
    baseline_path: Path,
) -> tuple[EvaluationManifest, Any, list[dict[str, Any]]]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    manifest = EvaluationManifest.model_validate(baseline["manifest"])
    manifest.enforce_limit(MAX_IMAGES)
    source_records = baseline.get("records")
    if not isinstance(source_records, list):
        raise ValueError("baseline records must be a list")
    by_id: dict[str, Mapping[str, Any]] = {}
    for record in source_records:
        sample_id = record.get("sample_id") if isinstance(record, Mapping) else None
        if not isinstance(sample_id, str) or sample_id in by_id:
            raise ValueError("baseline record identities must be unique")
        by_id[sample_id] = record
    expected_ids = [sample.sample_id for sample in manifest.samples]
    if set(by_id) != set(expected_ids):
        raise ValueError("baseline must contain one cached observation per manifest sample")

    observations = []
    for sample in manifest.samples:
        raw = by_id[sample.sample_id].get("observation")
        observation = ImageObservation.model_validate(raw)
        dimensions = (observation.detection.width, observation.detection.height)
        if dimensions != (sample.width, sample.height):
            raise ValueError(f"cached observation dimensions differ for {sample.sample_id}")
        observations.append(
            {
                "sample_id": sample.sample_id,
                "observation": observation.model_dump(mode="json"),
            }
        )
    return manifest, _json_copy(baseline.get("pins", {})), observations


def _validate_records(
    records: Any,
    manifest: EvaluationManifest,
    observations: Sequence[Mapping[str, Any]],
) -> None:
    if not isinstance(records, list):
        raise ValueError("saved records must be a list")
    expected = {item["sample_id"]: item["observation"] for item in observations}
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _RECORD_KEYS:
            raise ValueError("saved record schema does not match guided evaluation")
        sample_id = record["sample_id"]
        if sample_id not in expected or sample_id in seen:
            raise ValueError("saved record identity does not match manifest")
        seen.add(sample_id)
        observation = ImageObservation.model_validate(record["observation"])
        if observation.model_dump(mode="json") != expected[sample_id]:
            raise ValueError("saved record does not match cached observations")
        if record["review"] is not None:
            GuidedReview.model_validate(record["review"])
        if record["review_error"] is not None and not isinstance(record["review_error"], str):
            raise ValueError("saved review error must be a safe code")
        for prediction in record["predictions"]:
            LabeledBox.model_validate(prediction)
        duration = record["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("saved duration must be a finite nonnegative number")
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("saved duration must be a finite nonnegative number")
    manifest_ids = {sample.sample_id for sample in manifest.samples}
    if not seen <= manifest_ids:
        raise ValueError("saved record identity does not match manifest")


def _initialize_or_validate(
    output_dir: Path,
    manifest: EvaluationManifest,
    source_pins: Any,
    observations: list[dict[str, Any]],
    run_pins: Mapping[str, Any],
) -> dict[str, Any]:
    state_path = output_dir / "state.json"
    manifest_json = manifest.model_dump(mode="json")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("saved state schema does not match guided evaluation")
        if state.get("manifest") != manifest_json:
            raise ValueError("saved manifest does not match baseline identity")
        if state.get("source_pins") != source_pins:
            raise ValueError("saved source pins do not match baseline source pins")
        if state.get("observations") != observations:
            raise ValueError("saved cached observations do not match baseline cached observations")
        if state.get("run_pins") != run_pins:
            raise ValueError("saved guided pins do not match requested guided pins")
        if state.get("at_least_once") != AT_LEAST_ONCE_NOTICE:
            raise ValueError("saved execution semantics do not match guided evaluation")
    else:
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "manifest": manifest_json,
            "source_pins": source_pins,
            "observations": observations,
            "run_pins": _json_copy(run_pins),
            "at_least_once": AT_LEAST_ONCE_NOTICE,
            "records": [],
        }
        _write_json(state_path, state)
    _validate_records(state.get("records"), manifest, observations)
    return state


async def run_cached_evaluation(
    baseline_path: Path,
    output_dir: Path,
    *,
    reviewer: Any,
    provider_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Review each exact cached observation once and checkpoint after every image."""
    manifest, source_pins, observations = _load_baseline(Path(baseline_path))
    run_pins = _run_pins(provider_settings)
    state = _initialize_or_validate(Path(output_dir), manifest, source_pins, observations, run_pins)
    observations_by_id = {item["sample_id"]: item["observation"] for item in observations}
    completed = {record["sample_id"] for record in state["records"]}
    for sample in manifest.samples:
        if sample.sample_id in completed:
            continue
        observation = ImageObservation.model_validate(observations_by_id[sample.sample_id])
        start_sample = getattr(reviewer, "start_sample", None)
        if start_sample is not None:
            start_sample(sample.sample_id)
        review: GuidedReview | None = None
        review_error: str | None = None
        started = perf_counter()
        try:
            review = GuidedReview.model_validate(
                await reviewer.review(sample.image_path, observation)
            )
        except Exception as error:  # provider bodies and exception text are never persisted
            review_error = _safe_error(error)
        duration = perf_counter() - started
        fused = fuse_guided(observation, review)
        state["records"].append(
            {
                "sample_id": sample.sample_id,
                "observation": observation.model_dump(mode="json"),
                "review": review.model_dump(mode="json") if review is not None else None,
                "review_error": review_error,
                "predictions": fused["predictions"],
                "changes": fused["changes"],
                "duration_seconds": duration,
            }
        )
        _write_json(Path(output_dir) / "state.json", state)
    return state


def summarize_guided(
    manifest: EvaluationManifest,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score raw and fused boxes per image, including failed reviews as fallback."""
    by_id = {record["sample_id"]: record for record in records}
    expected_ids = [sample.sample_id for sample in manifest.samples]
    evaluated_ids = [sample_id for sample_id in expected_ids if sample_id in by_id]
    missing_ids = [sample_id for sample_id in expected_ids if sample_id not in by_id]
    raw_items: list[tuple[Sequence[LabeledBox], Sequence[LabeledBox]]] = []
    fused_items: list[tuple[Sequence[LabeledBox], Sequence[LabeledBox]]] = []
    per_image = []
    attribution = {
        "raw_detector_tp": 0,
        "lost_raw_detector_tp": 0,
        "raw_missed_ground_truth": 0,
        "recovered_raw_missed_ground_truth": 0,
        "zero_detection_image_count": 0,
        "zero_detection_ground_truth": 0,
        "recovered_on_zero_detection_images": 0,
    }
    error_count = pending_changes = 0
    duration = 0.0

    for sample in manifest.samples:
        record = by_id.get(sample.sample_id)
        if record is None:
            continue
        targets = list(sample.ground_truth)
        observation = ImageObservation.model_validate(record["observation"])
        raw = _raw_boxes(observation.detection)
        fused = [LabeledBox.model_validate(item) for item in record["predictions"]]
        raw_items.append((targets, raw))
        fused_items.append((targets, fused))
        raw_score = score_boxes(targets, raw)["micro"]
        fused_score = score_boxes(targets, fused)["micro"]
        per_image.append(
            {
                "sample_id": sample.sample_id,
                "raw_detector": raw_score,
                "fused_predictions": fused_score,
            }
        )

        raw_target_indices = {target for _, target in _matched_pairs(targets, raw)}
        fused_target_indices = {target for _, target in _matched_pairs(targets, fused)}
        missed = set(range(len(targets))) - raw_target_indices
        recovered = missed & fused_target_indices
        attribution["raw_detector_tp"] += len(raw_target_indices)
        attribution["lost_raw_detector_tp"] += len(raw_target_indices - fused_target_indices)
        attribution["raw_missed_ground_truth"] += len(missed)
        attribution["recovered_raw_missed_ground_truth"] += len(recovered)
        if not raw:
            attribution["zero_detection_image_count"] += 1
            attribution["zero_detection_ground_truth"] += len(targets)
            attribution["recovered_on_zero_detection_images"] += len(recovered)

        error_count += int(record.get("review_error") is not None)
        pending_changes += sum(bool(change.get("pending")) for change in record["changes"])
        duration += float(record["duration_seconds"])

    raw_total = _merge_scores(raw_items)
    fused_total = _merge_scores(fused_items)
    strict_improvement = (
        fused_total["micro"]["precision"] > raw_total["micro"]["precision"]
        and fused_total["micro"]["recall"] > raw_total["micro"]["recall"]
    )
    return {
        "status": "complete" if not missing_ids else "incomplete",
        "complete": not missing_ids,
        "expected_sample_count": len(expected_ids),
        "evaluated_sample_count": len(evaluated_ids),
        "evaluated_sample_ids": evaluated_ids,
        "missing_sample_count": len(missing_ids),
        "missing_sample_ids": missing_ids,
        "raw_detector": raw_total,
        "fused_predictions": fused_total,
        "per_image": per_image,
        "attribution": attribution,
        "review_error_count": error_count,
        "pending_sample_count": len(missing_ids),
        "pending_change_count": pending_changes,
        "review_attempt_count": len(evaluated_ids),
        "duration_seconds": duration,
        "comparison": {"precision_and_recall_strictly_higher": strict_improvement},
    }


class EvidenceCapturingClient:
    """Save credential-free request evidence around a structured client call."""

    def __init__(
        self,
        client: Any,
        request_root: Path,
        *,
        provider_settings: Mapping[str, Any],
    ) -> None:
        self._client = client
        self._request_root = Path(request_root)
        self._provider = _validate_nonsecret_settings(provider_settings)
        self._sample_id: str | None = None

    def start_sample(self, sample_id: str) -> None:
        self._sample_id = sample_id

    def _next_request_dir(self) -> Path:
        existing = [
            int(path.name)
            for path in self._request_root.glob("[0-9][0-9][0-9][0-9]")
            if path.is_dir() and path.name.isdigit()
        ]
        return self._request_root / f"{max(existing, default=0) + 1:04d}"

    @staticmethod
    def _externalize_messages(
        messages: Sequence[Mapping[str, Any]], request_dir: Path
    ) -> tuple[list[dict[str, Any]], list[str]]:
        externalized = _json_copy(list(messages))
        view_files: list[str] = []
        for message in externalized:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if item.get("type") != "image_url":
                    continue
                url = item.get("image_url", {}).get("url")
                if not isinstance(url, str) or not url.startswith("data:"):
                    raise APIClientError("invalid_request", retryable=False)
                header, separator, encoded = url.partition(",")
                media_type = header[5:].split(";", 1)[0]
                extension = {"image/png": "png", "image/jpeg": "jpg"}.get(media_type)
                if not separator or extension is None or ";base64" not in header:
                    raise APIClientError("invalid_request", retryable=False)
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except ValueError:
                    raise APIClientError("invalid_request", retryable=False) from None
                filename = f"view-{len(view_files)}.{extension}"
                (request_dir / filename).write_bytes(raw)
                item.pop("image_url", None)
                item["media_type"] = media_type
                item["image_file"] = filename
                view_files.append(filename)
        return externalized, view_files

    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[BaseModel],
    ) -> BaseModel:
        if self._sample_id is None:
            raise RuntimeError("start_sample must be called before generate")
        request_dir = self._next_request_dir()
        request_dir.mkdir(parents=True, exist_ok=False)
        externalized, view_files = self._externalize_messages(messages, request_dir)
        payload: dict[str, Any] = {
            "model": self._provider["model"],
            "messages": externalized,
            "response_format": {"type": "json_object"},
        }
        if self._provider.get("api_enable_thinking") is not None:
            payload["enable_thinking"] = self._provider["api_enable_thinking"]
        if self._provider.get("api_max_output_tokens") is not None:
            payload["max_tokens"] = self._provider["api_max_output_tokens"]
        endpoint = self._provider["base_url"].rstrip("/") + "/chat/completions"
        _write_json(
            request_dir / "request.json",
            {
                "sample_id": self._sample_id,
                "endpoint": endpoint,
                "response_model": response_model.__name__,
                "payload": payload,
                "view_files": view_files,
                "credentials_included": False,
                "at_least_once": AT_LEAST_ONCE_NOTICE,
            },
        )
        try:
            result = await self._client.generate(messages, response_model)
        except Exception as error:
            _write_json(request_dir / "error.json", {"code": _safe_error(error)})
            raise
        validated = response_model.model_validate(result)
        _write_json(request_dir / "response.json", validated.model_dump(mode="json"))
        return validated


class _CapturedGuidedReviewer:
    def __init__(self, reviewer: GuidedImageReviewer, client: EvidenceCapturingClient) -> None:
        self._reviewer = reviewer
        self._client = client

    def start_sample(self, sample_id: str) -> None:
        self._client.start_sample(sample_id)

    async def review(self, path: Path, observation: ImageObservation) -> GuidedReview:
        return await self._reviewer.review(path, observation)


def request_accounting(output_dir: Path) -> dict[str, int | None]:
    """Count persisted request evidence, not records or presumed billable calls.

    request.json precedes submission; a crash in that gap leaves an unresolved
    request, not proof of provider acceptance. Local pre-request failures create
    no request evidence. Resumed submissions have separate request directories.
    """
    requests = list((output_dir / "requests").glob("*/request.json"))
    responses = sum((request.parent / "response.json").is_file() for request in requests)
    errors = sum((request.parent / "error.json").is_file() for request in requests)
    unresolved = sum(
        not (request.parent / "response.json").is_file()
        and not (request.parent / "error.json").is_file()
        for request in requests
    )
    return {
        "request_records": len(requests),
        "validated_responses": responses,
        "client_errors": errors,
        "unresolved_requests": unresolved,
        "billable_calls": None,
    }


def build_live_reviewer(settings: Any, output_dir: Path) -> tuple[Any, dict[str, Any]]:
    """Build only the guided VLM boundary and return its nonsecret settings."""
    if not settings.vlm_configured:
        raise ValueError("complete VLM configuration is required for --live")
    parsed = urlsplit(settings.vlm_base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("VLM configuration contains an unsafe base URL")
    provider = {
        "base_url": settings.vlm_base_url,
        "model": settings.vlm_model,
        "api_enable_thinking": settings.api_enable_thinking,
        "api_max_output_tokens": settings.api_max_output_tokens,
        "api_timeout_seconds": settings.api_timeout_seconds,
        "vlm_max_image_bytes": settings.vlm_max_image_bytes,
    }
    client = OpenAICompatibleClient(
        settings.vlm_base_url,
        settings.vlm_model,
        settings.vlm_api_key,
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )
    captured = EvidenceCapturingClient(
        client, Path(output_dir) / "requests", provider_settings=provider
    )
    reviewer = GuidedImageReviewer(captured, max_image_bytes=settings.vlm_max_image_bytes)
    return _CapturedGuidedReviewer(reviewer, captured), provider


def _load_saved_state(baseline_path: Path, output_dir: Path) -> tuple[EvaluationManifest, dict]:
    state_path = output_dir / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError("saved guided state does not exist; use --live to run")
    manifest, source_pins, observations = _load_baseline(baseline_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    saved_provider = state.get("run_pins", {}).get("provider")
    if not isinstance(saved_provider, Mapping):
        raise ValueError("saved guided provider pins are missing")
    return manifest, _initialize_or_validate(
        output_dir, manifest, source_pins, observations, _run_pins(saved_provider)
    )


def cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review or offline-score exact cached detector observations."
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly permit at most one paid provider attempt per uncached image.",
    )
    args = parser.parse_args(argv)
    if args.live:
        # Delayed import keeps the default offline path free of configuration/secret access.
        from pipe_inspector.config import Settings

        reviewer, provider = build_live_reviewer(Settings(), args.output)
        state = asyncio.run(
            run_cached_evaluation(
                args.baseline,
                args.output,
                reviewer=reviewer,
                provider_settings=provider,
            )
        )
        manifest = EvaluationManifest.model_validate(state["manifest"])
    else:
        manifest, state = _load_saved_state(args.baseline, args.output)
    summary = summarize_guided(manifest, state["records"])
    summary["provider_evidence"] = request_accounting(args.output)
    _write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["complete"] else 2
