"""Explicit, repeat-safe start of an admitted image task."""

import asyncio
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
from pipe_inspector.config import Settings
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.planning import TaskPlan
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.vision.detector import UltralyticsDetector

POLICY_VERSION = "image-policy-v1"
PROMPT_VERSION = "image-review-v1"
DETECT_TIMEOUT = 180


def run_config(
    settings: Settings, detector=None, *, force_review: bool = False, plan: TaskPlan | None = None
) -> dict:
    if settings.vlm_base_url:
        try:
            url = httpx.URL(settings.vlm_base_url)
            valid = (
                url.scheme in {"http", "https"}
                and bool(url.host)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise InspectionStartError(
                "invalid_vlm_configuration", "VLM Base URL 配置无效；凭据只能放 API Key 字段"
            )
    detector = detector or UltralyticsDetector(
        settings.detector_path, device=settings.detector_device
    )
    return {
        "detector_id": detector.detector_id,
        "model_version": detector.model_version,
        "label_space_version": detector.label_space_version,
        "vlm_model": settings.vlm_model,
        "vlm_base_url": settings.vlm_base_url,
        "policy_version": POLICY_VERSION,
        "prompt_version": PROMPT_VERSION,
        "api_timeout_seconds": settings.api_timeout_seconds,
        "api_enable_thinking": settings.api_enable_thinking,
        "api_max_output_tokens": settings.api_max_output_tokens,
        "max_image_bytes": settings.vlm_max_image_bytes,
        "detect_call_id": str(uuid4()),
        "verify_call_id": str(uuid4()),
        "gpu_queue": settings.task_queue + "-gpu",
        "force_review": force_review,
        "plan": plan.model_dump(mode="json") if plan is not None else None,
    }


class InspectionStartError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        self.code, self.status_code = code, status_code
        super().__init__(message)


class InspectionStarter:
    def __init__(self, settings, repository, *, connect=Client.connect):
        self.settings, self.repo, self.connect = settings, repository, connect

    async def admit_supplement(self, parent_task_id: UUID, submission: TaskSubmission):
        raw = await asyncio.to_thread(self.repo.load_step, parent_task_id, "detect")
        try:
            observation = ImageObservation.model_validate(raw)
        except ValidationError:
            raise SupplementNotAllowed("source_evidence_missing") from None
        config = run_config(self.settings, detector=observation.detection, force_review=True)
        config.update(
            operation="supplement_review",
            parent_task_id=str(parent_task_id),
            supplement_policy_version=SUPPLEMENT_POLICY_VERSION,
        )
        return await asyncio.to_thread(
            self.repo.admit_supplement, parent_task_id, submission, config
        )

    async def start(
        self, task_id: UUID, *, force_review: bool = False, plan: TaskPlan | None = None
    ):
        record = await asyncio.to_thread(self.repo.get_task, task_id)
        asset = await asyncio.to_thread(self.repo.get_asset, record.input_asset)
        if asset.kind != "image":
            raise InspectionStartError(
                "image_only", "当前执行器只支持图片；视频执行将在后续阶段接入", 409
            )
        if record.snapshot.is_terminal and plan is None:
            return record
        run = await asyncio.to_thread(self.repo.get_run, task_id)
        parent_task_id = getattr(record, "parent_task_id", None)
        if parent_task_id is not None and (
            run is None
            or run["config"].get("operation") != "supplement_review"
            or run["config"].get("parent_task_id") != str(parent_task_id)
            or run["config"].get("force_review") is not True
        ):
            raise InspectionStartError(
                "supplement_run_invalid", "关联复检的固定运行记录无效，不能改为重新检测", 409
            )
        if run is None and not record.snapshot.is_terminal:
            try:
                run = await asyncio.to_thread(
                    self.repo.ensure_run,
                    task_id,
                    run_config(self.settings, force_review=force_review, plan=plan),
                )
            except SubmissionConflict:
                # A concurrent start won admission; UUID call IDs/config are already pinned.
                run = await asyncio.to_thread(self.repo.get_run, task_id)
                if run is None:
                    raise
        if plan is not None and (
            run is None
            or run["config"].get("plan") != plan.model_dump(mode="json")
            or run["config"].get("force_review", False) != force_review
        ):
            raise InspectionStartError(
                "plan_conflict", "任务已按另一执行计划启动，不能改写；请重新发起检查", 409
            )
        if record.snapshot.is_terminal:
            return record
        try:
            async with asyncio.timeout(10):
                client = await self.connect(
                    self.settings.temporal_address, namespace=self.settings.temporal_namespace
                )
                await client.start_workflow(
                    "ImageSupplementWorkflow" if parent_task_id else "ImageInspectionWorkflow",
                    run,
                    id=f"inspection:{task_id}",
                    task_queue=self.settings.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
        except WorkflowAlreadyStartedError:
            pass
        except (RPCError, OSError, TimeoutError, RuntimeError):
            raise InspectionStartError(
                "workflow_unavailable", "工作流启动暂未确认，请重试；重试不会创建第二个任务"
            ) from None
        return await asyncio.to_thread(self.repo.get_task, task_id)
