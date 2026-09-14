"""Pinned local geometry execution with durable source and evidence checkpoints."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.recall_policy import decide_image_recall
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.geometry import (
    GeometryOptions,
    RelativeDepthRequest,
    SegmentationRequest,
    SegmentationResult,
)
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview, InspectionDecision
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.vision.geometry import measure_geometry, run_relative_depth, run_segmentation
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.geometry_config import (
    GEOMETRY_ONLINE_VERSION,
    GEOMETRY_TIMEOUT,
    build_geometry_config,
    validate_geometry_input,
)
from pipe_inspector.workflow.start import InspectionStartError


def _linked_baseline(task_id: UUID, parent: TaskRecord) -> InspectionDecision:
    snapshot = parent.snapshot
    return InspectionDecision(
        findings=tuple(
            Finding.model_validate(
                finding.model_dump()
                | {
                    "task_id": task_id,
                    "finding_id": uuid5(task_id, str(finding.finding_id)),
                }
            )
            for finding in snapshot.findings
        ),
        inspection_result=snapshot.inspection_result,
        coverage=snapshot.coverage,
        needs_review=snapshot.runtime_status != "completed",
        reasons=(snapshot.waiting_reason,) if snapshot.waiting_reason else (),
    )


def geometry_baseline(repo, run, task, observation, review) -> InspectionDecision:
    task_id = task.snapshot.task_id
    saved = repo.load_step(task_id, "geometry_source")
    if saved is not None:
        return InspectionDecision.model_validate(saved["decision"])
    if "frame_source" in run["config"]:
        from pipe_inspector.workflow.frame_tools import frozen_frame_context

        return frozen_frame_context(repo, run, task)[2]
    if run["config"].get("operation") == "supplement_review":
        parent = TaskRecord.model_validate(repo.load_step(task_id, "source")["task"])
        return _linked_baseline(task_id, parent)
    return decide_image_recall(task_id, task.input_asset, observation, review)


class GeometryActivities(ImageActivities):
    def __init__(
        self,
        settings,
        repository,
        media,
        *,
        segmenter_factory=None,
        depth_factory=None,
    ) -> None:
        self.settings, self.repo, self.media = settings, repository, media
        self.segmenter_factory = segmenter_factory
        self.depth_factory = depth_factory
        self.detector = None
        self.reviewer = None

    def _call(self, run: dict, step: str, **changes) -> ToolCall:
        geometry = run["config"]["geometry"]
        pin = geometry["segment" if step == "geometry_segment" else "depth"]
        task = self.repo.get_task(UUID(run["task_id"]))
        operation = geometry["options"]["operation"]
        return ToolCall(
            call_id=pin["call_id"],
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name=(
                "segment"
                if step == "geometry_segment"
                else "relative_measure"
                if operation == "relative_measure"
                else "relative_depth"
            ),
            tool_version="1",
            resource="gpu",
            model_version=pin["model_version"],
            timeout_seconds=GEOMETRY_TIMEOUT,
            input_assets=(task.input_asset,),
            **changes,
        )

    def _check_geometry(self, run: dict):
        try:
            geometry = run["config"]["geometry"]
            options = GeometryOptions.model_validate(geometry["options"])
            expected = build_geometry_config(self.settings, options, check_files=False)
            if geometry.get("version") != GEOMETRY_ONLINE_VERSION:
                raise ValueError("version mismatch")
            for key in ("segment", "depth"):
                pinned = geometry.get(key)
                wanted = expected[key]
                if (pinned is None) != (wanted is None):
                    raise ValueError("model selection mismatch")
                if pinned is not None and any(
                    pinned.get(field) != value
                    for field, value in wanted.items()
                    if field != "call_id"
                ):
                    raise ValueError("model pin mismatch")
            task_id = UUID(run["task_id"])
            task = self.repo.get_task(task_id)
            asset = self.repo.get_asset(task.input_asset)
            selector_task = task
            frame_tool = "frame_source" in run["config"]
            if frame_tool:
                from pipe_inspector.workflow.frame_tools import validate_frame_tool_context

                validate_frame_tool_context(self.repo, run, task, asset)
            elif run["config"].get("operation") == "supplement_review":
                source = self.repo.load_step(task_id, "source")
                selector_task = TaskRecord.model_validate(source["task"])
            prompt = validate_geometry_input(
                asset, options, task=selector_task, allow_frame=frame_tool
            )
        except (KeyError, TypeError, ValueError, InspectionStartError):
            raise ApplicationError(
                "Geometry configuration changed",
                type="configuration_changed",
                non_retryable=True,
            ) from None
        return options, task, asset, prompt, selector_task

    def _stage(self, run: dict, name: str) -> None:
        current = self.repo.get_task(UUID(run["task_id"])).snapshot
        if current.is_terminal:
            return
        self.repo.update_snapshot(
            TaskSnapshot.model_validate(
                current.model_dump()
                | {
                    "runtime_status": "running",
                    "stage": "tools",
                    "active_tool": name,
                    "stage_progress": 0,
                    "overall_progress": 0.82 if name == "relative_depth" else 0.75,
                    "retry_count": max(current.retry_count, activity.info().attempt - 1),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    @activity.defn(name="prepare_image_geometry")
    def prepare_image_geometry(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "geometry_source")
        if cached is not None:
            return cached
        self._check_versions(run["config"])
        _, task, _, prompt, selector_task = self._check_geometry(run)
        if run["config"].get("operation") == "supplement_review":
            observation = review = None
        else:
            observation = ImageObservation.model_validate(self.repo.load_step(task_id, "detect"))
            saved = self.repo.load_step(task_id, "verify")
            review = (
                ImageReview.model_validate(saved["review"])
                if saved and saved.get("review")
                else None
            )
        baseline = geometry_baseline(self.repo, run, task, observation, review)
        return self.repo.save_step(
            task_id,
            "geometry_source",
            {
                "decision": baseline.model_dump(mode="json"),
                "input_asset": task.input_asset.model_dump(mode="json"),
                "prompt": None if prompt is None else prompt.model_dump(mode="json"),
            },
        )

    @activity.defn(name="segment_image")
    def segment_image(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "geometry_segment")
        if cached is not None:
            return cached
        self._stage(run, "segment")
        self.repo.record_call(self._call(run, "geometry_segment", status="running"))
        try:
            options, _, asset, prompt, _ = self._check_geometry(run)
            if options.operation not in {"segment", "relative_measure"} or prompt is None:
                raise ApplicationError(
                    "Segmentation is not pinned for this run",
                    type="configuration_changed",
                    non_retryable=True,
                )
            pin = run["config"]["geometry"]["segment"]
            factory = self.segmenter_factory
            if factory is None:
                from pipe_inspector.vision.geometry_models import LocalSegmenter

                factory = LocalSegmenter
            model = factory(Path(pin["checkpoint"]), device=pin["device"])
            if (model.model_id, model.model_version) != (
                pin["model_id"],
                pin["model_version"],
            ):
                raise ValueError("model metadata mismatch")
            result = run_segmentation(
                SegmentationRequest(input_asset=asset.ref, prompt=prompt),
                asset,
                self.media,
                model,
            )
            measurements = measure_geometry(result, self.media)
        except ApplicationError as exc:
            raise self._error(run, "geometry_segment", exc.type or "segmentation_failed") from None
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            raise self._error(run, "geometry_segment", "segmentation_failed") from None
        return self.repo.save_geometry(
            task_id,
            "geometry_segment",
            result,
            self._call(
                run,
                "geometry_segment",
                status="succeeded",
                output_assets=(result.mask_asset.ref, result.overlay_asset.ref),
            ),
            measurements,
        )

    @activity.defn(name="estimate_relative_depth")
    def estimate_relative_depth(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "geometry_depth")
        if cached is not None:
            return cached
        self._stage(run, "relative_depth")
        self.repo.record_call(self._call(run, "geometry_depth", status="running"))
        try:
            options, _, asset, _, _ = self._check_geometry(run)
            if options.operation not in {"relative_depth", "relative_measure"}:
                raise ApplicationError(
                    "Relative depth is not pinned for this run",
                    type="configuration_changed",
                    non_retryable=True,
                )
            pin = run["config"]["geometry"]["depth"]
            factory = self.depth_factory
            if factory is None:
                from pipe_inspector.vision.geometry_models import LocalRelativeDepth

                factory = LocalRelativeDepth
            model = factory(Path(pin["checkpoint"]), device=pin["device"])
            if (model.model_id, model.model_version) != (
                pin["model_id"],
                pin["model_version"],
            ):
                raise ValueError("model metadata mismatch")
            result = run_relative_depth(
                RelativeDepthRequest(input_asset=asset.ref), asset, self.media, model
            )
            measurements = None
            if options.operation == "relative_measure":
                segment = self.repo.load_step(task_id, "geometry_segment")
                segmentation = SegmentationResult.model_validate(segment["result"])
                measurements = measure_geometry(segmentation, self.media, result)
        except ApplicationError as exc:
            raise self._error(run, "geometry_depth", exc.type or "relative_depth_failed") from None
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            raise self._error(run, "geometry_depth", "relative_depth_failed") from None
        return self.repo.save_geometry(
            task_id,
            "geometry_depth",
            result,
            self._call(
                run,
                "geometry_depth",
                status="succeeded",
                output_assets=(result.raw_asset.ref, result.preview_asset.ref),
            ),
            measurements,
        )
