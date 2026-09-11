"""Fixed-cache, local-only incremental evaluation; never calls a VLM API.

Successful and failed samples are durable. A process interruption between model
execution and checkpoint can repeat that local call (at-least-once, not exactly-once).
Combined predictions describe a review queue, not automatically confirmed defects.
"""

import argparse
import json
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.agent.open_scan import merge_open_candidates, open_scan_reason
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview, InspectionDecision
from pipe_inspector.contracts.open_scan import OpenDetectionBatch, OpenScanEvidence
from pipe_inspector.contracts.tasks import CoverageSummary
from pipe_inspector.evaluation.guided import FUSION_VERSION, fuse_guided
from pipe_inspector.evaluation.guided_run import _validate_nonsecret_settings, _write_json
from pipe_inspector.evaluation.review import (
    LABELS,
    EvaluationManifest,
    EvaluationSample,
    _raw_boxes,
)

VERSION = "fixed-incremental-v1"
MAX_SAMPLES = 56


def _review(sample):
    model = GuidedReview if sample["baseline_policy"] == FUSION_VERSION else ImageReview
    return model.model_validate(sample["review"]) if sample.get("review") else None


def validate_samples(samples: list[dict]) -> list[dict]:
    """Normalize coordinates/contracts before any model call; retain unknown annotations."""
    if not 1 <= len(samples) <= MAX_SAMPLES:
        raise ValueError(f"fixed evaluation requires 1..{MAX_SAMPLES} samples")
    ids, normalized = set(), []
    for raw in samples:
        sample = json.loads(json.dumps(raw))
        if sample["sample_id"] in ids:
            raise ValueError("duplicate sample_id")
        ids.add(sample["sample_id"])
        if sample["source_kind"] not in ("image", "video"):
            raise ValueError("invalid source_kind")
        if sample["baseline_policy"] not in (FUSION_VERSION, "saved-video-review-v1"):
            raise ValueError("unsupported baseline policy")
        validated = EvaluationSample.model_validate(
            {key: sample[key] for key in ("sample_id", "group_id", "image_path", "width", "height")}
            | {"ground_truth": sample["ground_truth"] or []}
        )
        observation = ImageObservation.model_validate(sample["observation"])
        if (observation.detection.width, observation.detection.height) != (
            validated.width,
            validated.height,
        ):
            raise ValueError("observation dimensions differ from sample")
        for box in sample["baseline_predictions"]:
            # Reuse the same class/score/bounds validation as original annotations.
            EvaluationSample.model_validate(validated.model_dump() | {"ground_truth": [box]})
        review = _review(sample)
        if review:
            seen = set()
            for candidate in review.candidates:
                if candidate.index in seen or candidate.index >= len(
                    observation.detection.detections
                ):
                    raise ValueError("invalid review candidate index")
                seen.add(candidate.index)
                if candidate.verdict == "revise" and candidate.proposed_label is None:
                    raise ValueError("revision requires proposed label")
            for anomaly in review.additional_anomalies:
                if anomaly.bbox_xyxy:
                    ImageRegion(
                        width=validated.width, height=validated.height, bbox_xyxy=anomaly.bbox_xyxy
                    )
        if sample["source_kind"] == "video":
            EvidenceRef(
                asset=AssetRef(asset_id=NAMESPACE_URL),
                frame_index=sample.get("frame_index"),
                timestamp_seconds=sample.get("timestamp_seconds"),
            )
            if sample.get("frame_index") is None:
                raise ValueError("video frame locator required")
        sample["observation"] = observation.model_dump(mode="json")
        sample["review"] = review.model_dump(mode="json") if review else None
        normalized.append(sample)
    return normalized


def load_guided_samples(path: Path) -> list[dict]:
    """Accept completed accepted-policy caches; never resample or overwrite their labels."""
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != "guided-cached-evaluation-v1":
        raise ValueError("unsupported guided cache")
    if state.get("run_pins", {}).get("fusion", {}).get("version") != FUSION_VERSION:
        raise ValueError("guided cache fusion pin differs from accepted policy")
    manifest = EvaluationManifest.model_validate(state["manifest"])
    records = {record["sample_id"]: record for record in state["records"]}
    if len(records) != len(state["records"]) or set(records) != {
        sample.sample_id for sample in manifest.samples
    }:
        raise ValueError("guided cache records must exactly cover manifest")
    samples = []
    for item in manifest.samples:
        record = records[item.sample_id]
        observation = ImageObservation.model_validate(record["observation"])
        review = GuidedReview.model_validate(record["review"]) if record["review"] else None
        fused = fuse_guided(observation, None if record["review_error"] else review)
        if json.loads(json.dumps(fused)) != {
            key: record[key] for key in ("predictions", "changes")
        }:
            raise ValueError("cached predictions do not match accepted fusion policy")
        samples.append(
            item.model_dump(mode="json")
            | dict(
                source_kind="image",
                observation=record["observation"],
                review=record["review"],
                review_error=record["review_error"],
                baseline_predictions=record["predictions"],
                changes=record["changes"],
                baseline_policy=FUSION_VERSION,
                source_pins=dict(
                    path=str(path.resolve()),
                    run_pins=state["run_pins"],
                    source_pins=state["source_pins"],
                    split_provenance=manifest.split_provenance,
                ),
            )
        )
    return samples


def load_video_samples(path: Path) -> list[dict]:
    cache = json.loads(path.read_text(encoding="utf-8"))
    provenance = cache.get("provenance", {})
    if (
        provenance.get("schema_version") != "t11-video-cache-v1"
        or provenance.get("baseline_policy") != "saved-video-review-v1"
    ):
        raise ValueError("unsupported recovered video cache")
    return cache["samples"]


def validate_fixed_cohort(samples: list[dict], manifest: dict) -> int:
    """The reusable runner is bounded; the T11 CLI additionally pins exact cohort identities."""
    expected = manifest.get("samples", [])
    keys = ("sample_id", "group_id", "source_kind", "baseline_policy")
    actual = [{key: sample.get(key) for key in keys} for sample in samples]
    if (
        manifest.get("benchmark_id") != "t11-sewer2403-video-v1"
        or len(expected) != 56
        or sum(s.get("source_kind") == "image" for s in expected) != 48
        or sum(s.get("source_kind") == "video" for s in expected) != 8
        or actual != expected
    ):
        raise ValueError("inputs differ from the fixed cohort manifest")
    return len(expected)


def _merge(sample, batch):
    task_id = uuid5(NAMESPACE_URL, VERSION + ":" + sample["sample_id"])
    asset = AssetRef(asset_id=uuid5(task_id, "source"))
    locator = {key: sample.get(key) for key in ("frame_index", "timestamp_seconds")}
    findings = tuple(
        Finding(
            finding_id=uuid5(task_id, f"baseline:{index}"),
            task_id=task_id,
            canonical_label=box["label"],
            score=box["score"],
            source="detector",
            review_status="needs_review",
            evidence=(
                EvidenceRef(
                    asset=asset,
                    region=ImageRegion(
                        width=sample["width"], height=sample["height"], bbox_xyxy=box["bbox_xyxy"]
                    ),
                    **locator,
                ),
            ),
        )
        for index, box in enumerate(sample["baseline_predictions"])
    )
    decision = InspectionDecision(
        findings=findings,
        inspection_result="inconclusive",
        coverage=CoverageSummary(
            sufficient_coverage=True, sufficient_quality=True, negative_audit_complete=False
        ),
        needs_review=True,
        reasons=(),
    )
    if batch is None:
        return [], [], list(sample["baseline_predictions"])
    merged = merge_open_candidates(
        decision, task_id=task_id, scans=[OpenScanEvidence(batch=batch, asset=asset, **locator)]
    )
    assert merged.findings[: len(findings)] == findings
    additions = merged.findings[len(findings) :]
    added = []
    for finding in additions:
        candidate = next(
            c
            for c in batch.candidates
            if c.label == finding.canonical_label
            and c.score == finding.score
            and c.bbox_xyxy == finding.evidence[0].region.bbox_xyxy
            and (c.label != "OTHER" or c.name == finding.open_label)
        )
        added.append(candidate.model_dump(mode="json"))
    combined = list(sample["baseline_predictions"]) + [
        {key: candidate[key] for key in ("label", "bbox_xyxy", "score")}
        for candidate in added
        if candidate["label"] in LABELS
    ]
    return added, [finding.model_dump(mode="json") for finding in additions], combined


def _record(sample, batch, status, error, duration):
    observation, review = ImageObservation.model_validate(sample["observation"]), _review(sample)
    trigger = open_scan_reason(observation, None if sample.get("review_error") else review)
    added, findings, combined = _merge(sample, batch)
    return dict(
        sample_id=sample["sample_id"],
        source_kind=sample["source_kind"],
        ground_truth=sample["ground_truth"],
        observation=sample["observation"],
        review=sample["review"],
        review_error=sample.get("review_error"),
        changes=sample["changes"],
        primary=[b.model_dump(mode="json") for b in _raw_boxes(observation.detection)],
        guided=sample["baseline_predictions"],
        combined=combined,
        open_added=added,
        open_findings=findings,
        open_batch=batch.model_dump(mode="json") if batch else None,
        open_status=status,
        open_error=error,
        open_duration_seconds=duration,
        trigger=trigger,
        review_cached=review is not None,
        review_abstained=sum(
            c.verdict in ("abstain", "need_more_context") for c in review.candidates
        )
        if review
        else 0,
        pending_change_count=sum(bool(c.get("pending")) for c in sample["changes"]),
    )


def _validate_batch(batch, sample, pins):
    if (batch.width, batch.height) != (sample["width"], sample["height"]) or any(
        getattr(batch, key) != value for key, value in pins["detector"].items()
    ):
        raise ValueError("open model output differs from pins/source")


def run_incremental(
    samples, output_dir: Path, *, detector, configuration: dict, unload_after_sample: bool = True
):
    """Run checkpoints; an offline session may explicitly own final model unloading."""
    samples = validate_samples(samples)
    _validate_nonsecret_settings(configuration)
    for key in ("confidence", "image_size", "device"):
        if key in configuration and hasattr(detector, key):
            if configuration[key] != getattr(detector, key):
                raise ValueError("detector parameters differ from configuration")
    for key, attribute in (
        ("model_path", "checkpoint"),
        ("text_encoder_path", "text_encoder_path"),
    ):
        if key in configuration and hasattr(detector, attribute):
            if Path(configuration[key]).resolve() != Path(getattr(detector, attribute)).resolve():
                raise ValueError("detector asset paths differ from configuration")
    pins = dict(
        version=VERSION,
        fusion_version=FUSION_VERSION,
        configuration=configuration,
        detector={
            key: getattr(detector, key)
            for key in ("detector_id", "model_version", "vocabulary_version")
        },
    )
    snapshot = dict(
        samples=samples,
        pins=pins,
        at_least_once=True,
        scope="fixed cached images and video frame probes; not full video scheduling",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "state.json"
    state = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else snapshot | {"records": []}
    )
    if {key: state.get(key) for key in snapshot} != snapshot:
        raise ValueError("evaluation snapshot changed; use a new output directory")
    by_id, seen = {s["sample_id"]: s for s in samples}, set()
    for record in state["records"]:
        sample_id = record["sample_id"]
        if sample_id not in by_id or sample_id in seen:
            raise ValueError("invalid cached record identity")
        seen.add(sample_id)
        batch = (
            OpenDetectionBatch.model_validate(record["open_batch"])
            if record["open_batch"]
            else None
        )
        if batch is not None:
            _validate_batch(batch, by_id[sample_id], pins)
        if record != _record(
            by_id[sample_id],
            batch,
            record["open_status"],
            record["open_error"],
            record["open_duration_seconds"],
        ):
            raise ValueError("cached record differs from reproducible fusion")
        if record["open_status"] not in ("skipped", "succeeded", "failed") or (
            (record["open_status"] == "succeeded") != (batch is not None)
        ):
            raise ValueError("invalid cached record status")
    _write_json(path, state)
    for sample in samples:
        if sample["sample_id"] in seen:
            continue
        observation, review = (
            ImageObservation.model_validate(sample["observation"]),
            _review(sample),
        )
        trigger = open_scan_reason(observation, None if sample.get("review_error") else review)
        batch, status, error, duration = None, "skipped", None, 0.0
        try:
            with Image.open(sample["image_path"]) as source:
                source.load()
                if source.size != (sample["width"], sample["height"]):
                    raise ValueError("source dimensions changed")
        except (OSError, ValueError):
            status, error = "failed", "source_unavailable"
        else:
            if trigger:
                start = time.monotonic()
                try:
                    batch = detector.predict_image(Path(sample["image_path"]))
                    _validate_batch(batch, sample, pins)
                    status = "succeeded"
                except Exception:
                    batch, status, error = None, "failed", "open_scan_failed"
                finally:
                    if unload_after_sample:
                        detector.unload()
                    duration = time.monotonic() - start
        state["records"].append(_record(sample, batch, status, error, duration))
        _write_json(path, state)
    return state


def cli_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixed-manifest", type=Path, required=True)
    parser.add_argument("--guided-state", type=Path, action="append", default=[])
    parser.add_argument("--video-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--text-encoder", type=Path, required=True)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()
    samples = [sample for path in args.guided_state for sample in load_guided_samples(path)]
    if args.video_cache:
        samples.extend(load_video_samples(args.video_cache))
    expected_count = validate_fixed_cohort(
        samples, json.loads(args.fixed_manifest.read_text(encoding="utf-8"))
    )
    from pipe_inspector.evaluation.incremental_gallery import export_incremental_gallery
    from pipe_inspector.evaluation.incremental_metrics import (
        export_review_queue,
        summarize_incremental,
    )
    from pipe_inspector.vision.open_detector import YOLOEOpenDetector

    detector = YOLOEOpenDetector(
        args.model.resolve(), args.text_encoder.resolve(), device=args.device
    )
    state = run_incremental(
        samples,
        args.output,
        detector=detector,
        configuration=dict(
            model_path=str(args.model.resolve()),
            text_encoder_path=str(args.text_encoder.resolve()),
            device=args.device,
            confidence=0.25,
            image_size=640,
        ),
    )
    summary = summarize_incremental(state["records"], expected_count=expected_count)
    _write_json(args.output / "summary.json", summary)
    export_review_queue(
        state["records"], args.output / "review-queue.csv", samples=state["samples"]
    )
    export_incremental_gallery(state["samples"], state["records"], args.output / "gallery")
    print(
        json.dumps(
            dict(output=str(args.output.resolve()), coverage=summary["coverage"]),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    cli_main()
