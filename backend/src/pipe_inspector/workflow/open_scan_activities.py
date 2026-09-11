"""Bounded, durable supplemental localization. No candidate can overwrite primary evidence."""

from datetime import datetime, timezone
from uuid import UUID, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError

from pipe_inspector.agent.open_scan import merge_open_candidates
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview
from pipe_inspector.contracts.open_scan import OpenDetectionBatch, OpenScanEvidence
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolError
from pipe_inspector.contracts.video import VideoScanResult


def apply_open_results(repo, run, decision):
    """Missing selected checkpoints are audit gaps, including interrupted Activities."""
    if not run["config"].get("open_scan"):
        return decision
    task_id = UUID(run["task_id"])
    plan = repo.load_step(task_id, "open_plan")
    incomplete = plan is None or plan["incomplete"]
    scans = []
    for index, item in enumerate(plan["selected"] if plan else []):
        raw = repo.load_step(task_id, f"open_scan:{index}")
        if raw is None:
            incomplete = True
            continue
        scans.append(
            OpenScanEvidence(
                batch=OpenDetectionBatch.model_validate(raw["batch"]),
                asset=AssetRef.model_validate(item["asset"]),
                frame_index=item["frame_index"],
                timestamp_seconds=item["timestamp_seconds"],
            )
        )
    return merge_open_candidates(decision, task_id=task_id, scans=scans, incomplete=incomplete)


class OpenScanActivities:
    def __init__(self, settings, repository, media, *, detector=None, open_detector=None):
        self.settings, self.repo, self.media = settings, repository, media
        self.detector, self.open_detector = detector, open_detector

    def _check(self, run, *, model=False):
        config = run["config"].get("open_scan")
        if not config or config != self.settings.open_scan_config:
            raise ApplicationError(
                "Open scan configuration changed", type="configuration_changed", non_retryable=True
            )
        if model:
            if self.open_detector is None:
                from pipe_inspector.vision.open_detector import YOLOEOpenDetector

                self.open_detector = YOLOEOpenDetector(
                    self.settings.open_detector_path,
                    self.settings.open_text_encoder_path,
                    device=self.settings.detector_device,
                    image_size=config["image_size"],
                    confidence=config["confidence"],
                )
            if any(
                getattr(self.open_detector, key) != config[key]
                for key in ("detector_id", "model_version", "vocabulary_version")
            ):
                raise ApplicationError(
                    "Open model configuration changed",
                    type="configuration_changed",
                    non_retryable=True,
                )
        return config

    @staticmethod
    def _suspected(raw):
        if not raw or not raw.get("review"):
            return False
        review = ImageReview.model_validate(raw["review"])
        return bool(
            review.additional_anomalies
            or any(c.verdict == "need_more_context" for c in review.candidates)
        )

    @activity.defn(name="select_open_scan")
    def select_open_scan(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "open_plan")
        if cached is not None:
            return cached
        config = self._check(run)
        plan = run["config"].get("plan") or {}
        explicit = bool(
            plan.get("open_anomaly_audit")
            or any(s["action"] == "open_scan" for s in plan.get("steps", []))
        )
        budget = plan.get("budget") or {}
        used_calls = len(
            {c.idempotency_key for c in self.repo.list_calls(task_id) if c.status != "skipped"}
        )
        limit = min(config["max_frames"], max(0, budget.get("max_tool_calls", 32) - used_calls))
        if budget.get("per_event_open_scan", 1) == 0:
            limit = 0
        eligible, selected, seen_events = [], [], set()
        unavailable = 0
        video = run["config"].get("operation") == "video_inspection"
        if not video:
            raw = self.repo.load_step(task_id, "detect")
            if raw:
                obs = ImageObservation.model_validate(raw)
                reason = (
                    "explicit_request"
                    if explicit
                    else "suspected_miss"
                    if self._suspected(self.repo.load_step(task_id, "verify"))
                    else "negative_audit"
                    if obs.quality.adequate and not obs.detection.detections
                    else None
                )
                if reason:
                    eligible.append(
                        dict(
                            asset=self.repo.get_task(task_id).input_asset.model_dump(mode="json"),
                            frame_index=None,
                            timestamp_seconds=None,
                            reason=reason,
                            events=[],
                        )
                    )
            elif explicit:
                unavailable += 1
        else:
            raw = self.repo.load_step(task_id, "video_scan")
            evidence = self.repo.load_step(task_id, "video_evidence") or {"frames": []}
            if raw:
                scan = VideoScanResult.model_validate(raw)
                materialized = {f["frame_index"]: f for f in evidence["frames"]}
                audits = {f.frame_index for f in scan.state.audit_frames}
                explicit_frames = (
                    {event.evidence[0].frame_index for event in scan.state.events}
                    if explicit
                    else set()
                )
                indices = sorted(audits | set(materialized) | explicit_frames)
                for index in indices:
                    obs = self.repo.get_video_frame(task_id, index)
                    reason = (
                        "explicit_request"
                        if index in explicit_frames
                        else "suspected_miss"
                        if self._suspected(self.repo.load_step(task_id, f"video_verify:{index}"))
                        else "negative_audit"
                        if index in audits and obs.quality.adequate and not obs.detection.detections
                        else None
                    )
                    if not reason:
                        continue
                    if index not in materialized:
                        unavailable += 1
                        continue
                    eligible.append(
                        dict(
                            asset=materialized[index]["asset"],
                            frame_index=index,
                            timestamp_seconds=obs.timestamp_seconds,
                            reason=reason,
                            events=[
                                e.event_id
                                for e in scan.state.events
                                if e.start_frame <= index <= e.end_frame
                            ],
                        )
                    )
                # Explicit request on an unassessable/empty video is not fulfilled by an empty plan.
                if explicit and not eligible and not unavailable:
                    unavailable += 1
            else:
                unavailable += 1
        eligible.sort(
            key=lambda item: (
                {"explicit_request": 0, "suspected_miss": 1, "negative_audit": 2}[item["reason"]],
                item["frame_index"] or 0,
            )
        )
        deferred = unavailable
        for item in eligible:
            if seen_events.intersection(item["events"]):
                continue  # Another selected frame already satisfies this event's scan.
            if len(selected) >= limit:
                deferred += 1
                continue
            selected.append(item)
            seen_events.update(item["events"])
        result = dict(
            selected=selected,
            incomplete=bool(deferred),
            deferred_count=deferred,
            eligible_count=len(eligible) + unavailable,
            explicit=explicit,
            skip_reason=None
            if selected
            else "budget_or_evidence_gap"
            if deferred
            else "not_triggered",
        )
        skipped = (
            self._call(
                run,
                "selection",
                {"asset": self.repo.get_task(task_id).input_asset.model_dump(mode="json")},
                "skipped",
                skip_reason=result["skip_reason"],
            )
            if not selected
            else None
        )
        return self.repo.save_step(task_id, "open_plan", result, skipped)

    def _call(self, run, index, item, status, *, code=None, retryable=False, skip_reason=None):
        step = "open_plan" if index == "selection" else f"open_scan:{index}"
        config = run["config"]["open_scan"]
        return ToolCall(
            call_id=uuid5(UUID(run["run_id"]), step),
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name="open_scan",
            tool_version=config["version"],
            model_version=config["model_version"],
            resource="gpu",
            timeout_seconds=180,
            input_assets=(AssetRef.model_validate(item["asset"]),),
            status=status,
            skip_reason=skip_reason,
            error=ToolError(code=code, retryable=retryable, message=f"开放候选检测未完成：{code}")
            if code
            else None,
        )

    @activity.defn(name="scan_open_candidate")
    def scan_open_candidate(self, payload: dict) -> dict:
        run, index = payload["run"], payload["index"]
        task_id = UUID(run["task_id"])
        plan = self.repo.load_step(task_id, "open_plan")
        if not plan or not isinstance(index, int) or not 0 <= index < len(plan["selected"]):
            raise ApplicationError(
                "Unselected open scan", type="invalid_open_selection", non_retryable=True
            )
        item, step = plan["selected"][index], f"open_scan:{index}"
        cached = self.repo.load_step(task_id, step)
        if cached is not None:
            return dict(index=index, candidate_count=len(cached["batch"]["candidates"]))
        current = self.repo.get_task(task_id).snapshot
        if current.is_terminal:
            raise ApplicationError(
                "Task already terminal", type="task_terminal", non_retryable=True
            )
        self.repo.update_snapshot(
            TaskSnapshot.model_validate(
                current.model_dump()
                | dict(
                    runtime_status="running",
                    stage="tools",
                    active_tool="open_scan",
                    stage_progress=index / len(plan["selected"]),
                    overall_progress=max(current.overall_progress, 0.9),
                    updated_at=datetime.now(timezone.utc),
                    retry_count=max(current.retry_count, activity.info().attempt - 1),
                )
            )
        )
        self.repo.record_call(self._call(run, index, item, "running"))
        try:
            self._check(run, model=True)
            if activity.is_cancelled():
                raise CancelledError()
            if self.detector is not None and callable(getattr(self.detector, "unload", None)):
                self.detector.unload()
            batch = self.open_detector.predict_image(
                self.media.path_for(AssetRef.model_validate(item["asset"]))
            )
            batch = OpenDetectionBatch.model_validate(batch)
            original = (
                ImageObservation.model_validate(self.repo.load_step(task_id, "detect"))
                if item["frame_index"] is None
                else self.repo.get_video_frame(task_id, item["frame_index"])
            )
            if (batch.width, batch.height) != (original.detection.width, original.detection.height):
                raise ValueError("Open detector returned mismatched original dimensions")
            config = run["config"]["open_scan"]
            if any(
                getattr(batch, key) != config[key]
                for key in ("detector_id", "model_version", "vocabulary_version")
            ):
                raise ValueError("Open detector returned mismatched model metadata")
        except (ApplicationError, FileNotFoundError, ImportError, ValueError, RuntimeError) as exc:
            code = (
                exc.type
                if isinstance(exc, ApplicationError)
                else "open_model_unavailable"
                if isinstance(exc, (FileNotFoundError, ImportError))
                else "open_scan_failed"
            )
            retryable = isinstance(exc, RuntimeError) and not isinstance(exc, ApplicationError)
            self.repo.record_call(
                self._call(run, index, item, "failed", code=code, retryable=retryable)
            )
            raise ApplicationError(
                "Open candidate scan failed", type=code, non_retryable=not retryable
            ) from None
        finally:
            if self.open_detector is not None:
                self.open_detector.unload()
        saved = self.repo.save_step(
            task_id,
            step,
            {"batch": batch.model_dump(mode="json")},
            self._call(run, index, item, "succeeded"),
        )
        if activity.is_cancelled():
            raise CancelledError()
        return dict(index=index, candidate_count=len(saved["batch"]["candidates"]))
