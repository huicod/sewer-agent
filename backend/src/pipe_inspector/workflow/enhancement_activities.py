"""Pinned enhancement execution with durable baseline, asset and auxiliary review checkpoints."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.enhancement_review import EnhancementReviewer
from pipe_inspector.agent.recall_policy import decide_image_recall
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import (
    EnhancementOptions,
    EnhancementRequest,
    EnhancementResult,
)
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview, InspectionDecision
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.vision.enhancement import EnhancementError, run_enhancement
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.enhancement_config import (
    ENHANCEMENT_TIMEOUT,
    build_enhancement_config,
    validate_enhancement_input,
)
from pipe_inspector.workflow.start import InspectionStartError


def enhancement_baseline(repo, run, task, observation, review):
    task_id = task.snapshot.task_id
    saved = repo.load_step(task_id, "enhancement_source")
    if saved is not None:
        return InspectionDecision.model_validate(saved["decision"])
    if "frame_source" in run["config"]:
        from pipe_inspector.workflow.frame_tools import frozen_frame_context

        return frozen_frame_context(repo, run, task)[2]
    if run["config"].get("operation") != "supplement_review":
        return decide_image_recall(task_id, task.input_asset, observation, review)
    parent = TaskRecord.model_validate(repo.load_step(task_id, "source")["task"]).snapshot
    return InspectionDecision(
        findings=tuple(
            Finding.model_validate(
                f.model_dump()
                | {
                    "task_id": task_id,
                    "finding_id": uuid5(task_id, str(f.finding_id)),
                }
            )
            for f in parent.findings
        ),
        inspection_result=parent.inspection_result,
        coverage=parent.coverage,
        needs_review=parent.runtime_status != "completed",
        reasons=(parent.waiting_reason,) if parent.waiting_reason else (),
    )


class EnhancementActivities(ImageActivities):
    def __init__(self, settings, repository, media, *, model_factory=None, reviewer=None):
        # This activity group reuses call/version helpers but never owns a detector.
        self.settings, self.repo, self.media = settings, repository, media
        self.reviewer = reviewer
        self.model_factory = model_factory

    def _call(self, run, step, **changes):
        pin = run["config"]["enhancement"]
        task = self.repo.get_task(UUID(run["task_id"]))
        inputs = (task.input_asset,)
        if step == "enhancement_review":
            result = self.repo.load_step(task.snapshot.task_id, "enhance")
            if result:
                inputs += (EnhancementResult.model_validate(result).output_asset.ref,)
        return ToolCall(
            call_id=pin["call_id" if step == "enhance" else "review_call_id"],
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name="enhance" if step == "enhance" else "verify",
            tool_version="1",
            resource="gpu" if step == "enhance" else "api",
            model_version=(
                pin["model_version"] if step == "enhance" else run["config"]["vlm_model"]
            ),
            timeout_seconds=(
                ENHANCEMENT_TIMEOUT if step == "enhance" else run["config"]["api_timeout_seconds"]
            ),
            input_assets=inputs,
            **changes,
        )

    def _check_enhancement(self, run):
        pin = run["config"]["enhancement"]
        try:
            options = EnhancementOptions.model_validate(pin["options"])
            expected = build_enhancement_config(self.settings, options, check_files=False)
            if any(
                pin.get(key) != value
                for key, value in expected.items()
                if key not in {"call_id", "review_call_id"}
            ):
                raise ValueError("pin mismatch")
            task = self.repo.get_task(UUID(run["task_id"]))
            asset = self.repo.get_asset(task.input_asset)
            frame_tool = "frame_source" in run["config"]
            if frame_tool:
                from pipe_inspector.workflow.frame_tools import validate_frame_tool_context

                validate_frame_tool_context(self.repo, run, task, asset)
            validate_enhancement_input(asset, options, allow_frame=frame_tool)
        except (KeyError, ValueError, InspectionStartError):
            raise ApplicationError(
                "Enhancement configuration changed",
                type="configuration_changed",
                non_retryable=True,
            ) from None
        return options, asset

    def _stage(self, run, name):
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
                    "overall_progress": 0.85 if name == "enhancement_review" else 0.75,
                    "retry_count": max(current.retry_count, activity.info().attempt - 1),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
        )

    @activity.defn(name="prepare_image_enhancement")
    def prepare_image_enhancement(self, run):
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "enhancement_source")
        if cached is not None:
            return cached
        self._check_versions(run["config"])
        options, asset = self._check_enhancement(run)
        task = self.repo.get_task(task_id)
        observation = ImageObservation.model_validate(self.repo.load_step(task_id, "detect"))
        saved = self.repo.load_step(task_id, "verify")
        review = (
            ImageReview.model_validate(saved["review"]) if saved and saved.get("review") else None
        )
        baseline = enhancement_baseline(self.repo, run, task, observation, review)
        request = EnhancementRequest(
            input_asset=task.input_asset,
            kind=options.kind,
            roi=(
                ImageRegion(width=asset.width, height=asset.height, bbox_xyxy=options.roi_xyxy)
                if options.roi_xyxy is not None
                else None
            ),
        )
        return self.repo.save_step(
            task_id,
            "enhancement_source",
            {
                "decision": baseline.model_dump(mode="json"),
                "request": request.model_dump(mode="json"),
            },
        )

    @activity.defn(name="enhance_image")
    def enhance_image(self, run):
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "enhance")
        if cached is not None:
            return cached
        self._stage(run, run["config"]["enhancement"]["options"]["kind"])
        self.repo.record_call(self._call(run, "enhance", status="running"))
        try:
            self._check_enhancement(run)
            pin = run["config"]["enhancement"]
            request = EnhancementRequest.model_validate(
                self.repo.load_step(task_id, "enhancement_source")["request"]
            )
            factory = self.model_factory
            if factory is None:
                from pipe_inspector.vision.enhancement_models import LocalEnhancementModel

                factory = LocalEnhancementModel
            model = factory(request.kind, Path(pin["checkpoint"]), device=pin["device"])
            if (model.model_id, model.model_version) != (pin["model_id"], pin["model_version"]):
                raise EnhancementError("model_metadata_mismatch")
            result = run_enhancement(request, self.media, model)
        except ApplicationError as exc:
            raise self._error(run, "enhance", exc.type or "enhancement_failed") from None
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            raise self._error(run, "enhance", "enhancement_failed") from None
        # Commit failures stay retryable. Retry observes the winning asset checkpoint first.
        return self.repo.save_enhancement(
            task_id,
            result,
            self._call(
                run,
                "enhance",
                status="succeeded",
                output_assets=(result.output_asset.ref,),
            ),
        )

    @activity.defn(name="review_image_enhancement")
    async def review_image_enhancement(self, run):
        task_id = UUID(run["task_id"])
        cached = await asyncio.to_thread(self.repo.load_step, task_id, "enhancement_review")
        if cached is not None:
            return cached
        await asyncio.to_thread(self._stage, run, "enhancement_review")
        await asyncio.to_thread(
            self.repo.record_call,
            await asyncio.to_thread(self._call, run, "enhancement_review", status="running"),
        )
        try:
            self._check_versions(run["config"], vlm=True)
            await asyncio.to_thread(self._check_enhancement, run)
            result = EnhancementResult.model_validate(
                await asyncio.to_thread(self.repo.load_step, task_id, "enhance")
            )
            source = await asyncio.to_thread(self.repo.load_step, task_id, "enhancement_source")
            baseline = InspectionDecision.model_validate(source["decision"])
            reviewer = self.reviewer
            if reviewer is None:
                if not self.settings.vlm_configured:
                    raise APIClientError("vlm_not_configured", retryable=False)
                reviewer = EnhancementReviewer(
                    OpenAICompatibleClient(
                        self.settings.vlm_base_url,
                        self.settings.vlm_model,
                        self.settings.vlm_api_key,
                        timeout_seconds=run["config"]["api_timeout_seconds"],
                        enable_thinking=run["config"].get("api_enable_thinking"),
                        max_output_tokens=run["config"].get("api_max_output_tokens"),
                    ),
                    max_image_bytes=run["config"]["max_image_bytes"],
                )
            review = await reviewer.review(
                self.media.path_for(result.input_asset),
                self.media.path_for(result.output_asset.ref),
                result,
                baseline,
            )
        except APIClientError as exc:
            raise await asyncio.to_thread(
                self._error,
                run,
                "enhancement_review",
                exc.code,
                retryable=exc.retryable,
                uncertain=exc.code in {"timeout", "network_error"},
            ) from None
        except ApplicationError as exc:
            raise await asyncio.to_thread(
                self._error, run, "enhancement_review", exc.type or "enhancement_review_failed"
            ) from None
        except (OSError, ValueError, TypeError, KeyError):
            raise await asyncio.to_thread(
                self._error, run, "enhancement_review", "enhancement_review_input_invalid"
            ) from None
        return await asyncio.to_thread(
            self.repo.save_step,
            task_id,
            "enhancement_review",
            review.model_dump(mode="json"),
            await asyncio.to_thread(self._call, run, "enhancement_review", status="succeeded"),
        )
