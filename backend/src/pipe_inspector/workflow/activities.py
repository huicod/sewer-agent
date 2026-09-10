"""Small checkpointed activities; exceptions expose codes, never provider payloads."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.image_discovery import VIEW_POLICY_VERSION, IndependentImageReviewer
from pipe_inspector.agent.image_review import IMAGE_REVIEW_PROMPT_VERSION, ImageReviewer
from pipe_inspector.agent.recall_policy import decide_image_recall
from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import EvidenceRef, Finding, FindingSource, ReviewStatus
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview
from pipe_inspector.contracts.tasks import CoverageSummary, TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolCallStatus, ToolError
from pipe_inspector.vision.detector import UltralyticsDetector
from pipe_inspector.vision.quality import analyze_quality
from pipe_inspector.workflow.start import DETECT_TIMEOUT, POLICY_VERSION, PROMPT_VERSION


class ImageActivities:
    def __init__(self, settings, repository, media, *, detector=None, reviewer=None):
        self.settings, self.repo, self.media = settings, repository, media
        self.detector = detector or UltralyticsDetector(
            settings.detector_path, device=settings.detector_device
        )
        self.reviewer = reviewer

    def _call(self, run: dict, step: str, **changes) -> ToolCall:
        config = run["config"]
        task = self.repo.get_task(UUID(run["task_id"]))
        return ToolCall(
            call_id=config[f"{step}_call_id"],
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name=step,
            tool_version="1",
            resource="gpu" if step == "detect" else "api",
            model_version=config["model_version" if step == "detect" else "vlm_model"],
            timeout_seconds=DETECT_TIMEOUT if step == "detect" else config["api_timeout_seconds"],
            input_assets=(task.input_asset,),
            **changes,
        )

    def _check_versions(self, config, *, detector=False, vlm=False):
        independent = self.settings.image_review_mode == "independent_discovery"
        expected = {
            "policy_version": POLICY_VERSION,
            "prompt_version": PROMPT_VERSION if independent else IMAGE_REVIEW_PROMPT_VERSION,
            "view_policy_version": VIEW_POLICY_VERSION if independent else None,
            "image_review_mode": self.settings.image_review_mode,
        }
        if config.get("operation") == "video_inspection":
            expected = {
                "policy_version": "image-policy-v1",
                "prompt_version": IMAGE_REVIEW_PROMPT_VERSION,
            }
        if config.get("operation") == "supplement_review":
            expected["supplement_policy_version"] = SUPPLEMENT_POLICY_VERSION
        if detector:
            expected.update(
                {
                    key: getattr(self.detector, key)
                    for key in ("detector_id", "model_version", "label_space_version")
                }
            )
        if vlm:
            expected.update(
                vlm_model=self.settings.vlm_model,
                vlm_base_url=self.settings.vlm_base_url,
                api_timeout_seconds=self.settings.api_timeout_seconds,
                api_enable_thinking=self.settings.api_enable_thinking,
                api_max_output_tokens=self.settings.api_max_output_tokens,
                max_image_bytes=self.settings.vlm_max_image_bytes,
            )
        if any(config.get(key) != value for key, value in expected.items()):
            raise ApplicationError(
                "Worker configuration differs from pinned run",
                type="configuration_changed",
                non_retryable=True,
            )

    def _error(self, run, step, code, *, retryable=False, uncertain=False):
        call = self._call(
            run,
            step,
            status="uncertain" if uncertain else "failed",
            error=ToolError(code=code, retryable=retryable, message=f"工具执行未完成：{code}"),
        )
        self.repo.record_call(call)
        return ApplicationError(
            f"Inspection tool failed: {code}", type=code, non_retryable=not retryable
        )

    def _mark_running_calls_uncertain(self, task_id: UUID, code: str) -> None:
        for call in self.repo.list_calls(task_id):
            if call.status is not ToolCallStatus.RUNNING:
                continue
            uncertain = ToolCall.model_validate(
                call.model_dump()
                | {
                    "status": "uncertain",
                    "error": ToolError(
                        code=code,
                        retryable=True,
                        message=f"工具执行结果未确认：{code}",
                    ),
                }
            )
            self.repo.record_call(uncertain)

    @staticmethod
    def _configuration_changed_findings(task_id, asset, observation):
        return tuple(
            Finding(
                finding_id=uuid4(),
                task_id=task_id,
                canonical_label=detection.label,
                review_status=ReviewStatus.NEEDS_REVIEW,
                source=FindingSource.DETECTOR,
                score=detection.score,
                evidence=(
                    EvidenceRef(
                        asset=asset,
                        region=ImageRegion(
                            width=observation.detection.width,
                            height=observation.detection.height,
                            bbox_xyxy=detection.bbox_xyxy,
                        ),
                    ),
                ),
            )
            for detection in observation.detection.detections
        )

    @activity.defn(name="publish_image_stage")
    def publish_stage(self, payload: dict) -> None:
        task_id = UUID(payload["run"]["task_id"])
        current = self.repo.get_task(task_id).snapshot
        if current.is_terminal:
            return
        stage = payload["stage"]
        overall_progress, active_tool = {
            "detecting": (0.1, "detect"),
            "verifying": (0.6, "verify"),
            "reporting": (0.95, None),
        }[stage]
        snapshot = TaskSnapshot.model_validate(
            current.model_dump()
            | {
                "runtime_status": "running",
                "stage": stage,
                "stage_progress": 0,
                "overall_progress": overall_progress,
                "active_tool": active_tool,
                "updated_at": datetime.now(timezone.utc),
                "retry_count": max(current.retry_count, payload.get("retry_count", 0)),
            }
        )
        self.repo.update_snapshot(snapshot)

    @activity.defn(name="detect_image")
    def detect_image(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "detect")
        if cached is not None:
            return cached
        self.repo.record_call(self._call(run, "detect", status="running"))
        try:
            self._check_versions(run["config"], detector=True)
            task = self.repo.get_task(task_id)
            path = self.media.path_for(task.input_asset)
            detection = self.detector.predict_image(path)
            if any(
                getattr(detection, key) != run["config"][key]
                for key in ("detector_id", "model_version", "label_space_version")
            ):
                raise ApplicationError(
                    "Detector returned unpinned metadata",
                    type="configuration_changed",
                    non_retryable=True,
                )
            observation = ImageObservation(detection=detection, quality=analyze_quality(path))
        except ApplicationError as exc:
            raise self._error(run, "detect", exc.type or "detection_failed") from None
        except (OSError, ValueError, RuntimeError):
            # CUDA OOM/model incompatibility are not fixed by blindly repeating the same call.
            raise self._error(run, "detect", "detection_failed") from None
        return self.repo.save_step(
            task_id,
            "detect",
            observation.model_dump(mode="json"),
            self._call(run, "detect", status="succeeded"),
        )

    @activity.defn(name="verify_image")
    async def verify_image(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = await asyncio.to_thread(self.repo.load_step, task_id, "verify")
        if cached is not None:
            return cached
        observation = ImageObservation.model_validate(
            await asyncio.to_thread(self.repo.load_step, task_id, "detect")
        )
        await asyncio.to_thread(
            self.publish_stage,
            {"run": run, "stage": "verifying", "retry_count": activity.info().attempt - 1},
        )
        await asyncio.to_thread(
            self.repo.record_call,
            await asyncio.to_thread(self._call, run, "verify", status="running"),
        )
        try:
            self._check_versions(run["config"], vlm=True)
            reviewer = self.reviewer
            if reviewer is None:
                if not self.settings.vlm_configured:
                    raise APIClientError("vlm_not_configured", retryable=False)
                reviewer_type = (
                    IndependentImageReviewer
                    if run["config"]["image_review_mode"] == "independent_discovery"
                    else ImageReviewer
                )
                reviewer = reviewer_type(
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
            task = await asyncio.to_thread(self.repo.get_task, task_id)
            review = await reviewer.review(self.media.path_for(task.input_asset), observation)
        except APIClientError as exc:
            error = await asyncio.to_thread(
                self._error,
                run,
                "verify",
                exc.code,
                retryable=exc.retryable,
                uncertain=exc.code in {"timeout", "network_error"},
            )
            raise error from None
        except ApplicationError as exc:
            raise await asyncio.to_thread(
                self._error, run, "verify", exc.type or "review_failed"
            ) from None
        except (OSError, ValueError):
            raise await asyncio.to_thread(
                self._error, run, "verify", "review_input_invalid"
            ) from None
        call = await asyncio.to_thread(self._call, run, "verify", status="succeeded")
        return await asyncio.to_thread(
            self.repo.save_step,
            task_id,
            "verify",
            {"review": review.model_dump(mode="json"), "skipped": False},
            call,
        )

    def _decide(self, run, task, observation, review, error):
        return decide_image_recall(
            task.snapshot.task_id, task.input_asset, observation, review, review_error=error
        )

    @activity.defn(name="finish_image")
    def finish_image(self, payload: dict) -> dict:
        run = payload["run"]
        task_id = UUID(run["task_id"])
        error = payload.get("error")
        self.publish_stage({"run": run, "stage": "reporting"})
        self._mark_running_calls_uncertain(task_id, error or "activity_interrupted")
        cached = self.repo.load_step(task_id, "final")
        if cached is not None:
            self.repo.update_snapshot(TaskSnapshot.model_validate(cached))
            return cached
        task = self.repo.get_task(task_id)
        raw_observation = self.repo.load_step(task_id, "detect")
        values = task.snapshot.model_dump() | {
            "stage": "finished",
            "active_tool": None,
            "stage_progress": 1,
            "overall_progress": 1,
            "updated_at": datetime.now(timezone.utc),
        }
        if raw_observation is None:
            values.update(
                runtime_status="failed",
                inspection_result="inconclusive",
                waiting_reason=f"检测未完成：{error or 'detection_failed'}",
            )
        else:
            observation = ImageObservation.model_validate(raw_observation)
            try:
                self._check_versions(run["config"])
            except ApplicationError as exc:
                if exc.type != "configuration_changed":
                    raise
                values.update(
                    runtime_status="failed",
                    inspection_result="inconclusive",
                    findings=self._configuration_changed_findings(
                        task_id, task.input_asset, observation
                    ),
                    coverage=CoverageSummary(
                        sufficient_coverage=True,
                        sufficient_quality=observation.quality.adequate,
                        negative_audit_complete=False,
                    ),
                    processed_frames=1,
                    waiting_reason="检查配置已变化：configuration_changed",
                )
            else:
                saved_review = self.repo.load_step(task_id, "verify")
                review = (
                    ImageReview.model_validate(saved_review["review"])
                    if saved_review and saved_review.get("review")
                    else None
                )
                decision = self._decide(run, task, observation, review, error)
                partial = (
                    bool(error)
                    or decision.needs_review
                    or decision.inspection_result == "inconclusive"
                )
                waiting_reasons = list(decision.reasons)
                if error:
                    waiting_reasons.append(f"review_error:{error}")
                values.update(
                    runtime_status="partial" if partial else "completed",
                    inspection_result=decision.inspection_result,
                    findings=decision.findings,
                    coverage=decision.coverage,
                    processed_frames=1,
                    waiting_reason="；".join(waiting_reasons) or None,
                )
        snapshot = TaskSnapshot.model_validate(values)
        saved = self.repo.save_step(task_id, "final", snapshot.model_dump(mode="json"))
        self.repo.update_snapshot(TaskSnapshot.model_validate(saved))
        return saved
