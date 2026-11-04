"""Explicit, repeat-safe start and cancellation of admitted media tasks."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError
from temporalio.client import (
    Client,
    WorkflowUpdateFailedError,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from pipe_inspector.agent.detector_only_policy import DETECTOR_ONLY_POLICY_VERSION
from pipe_inspector.agent.image_discovery import DISCOVERY_PROMPT_VERSION
from pipe_inspector.agent.image_review import IMAGE_REVIEW_PROMPT_VERSION
from pipe_inspector.agent.recall_policy import RECALL_POLICY_VERSION
from pipe_inspector.agent.review_pipeline import review_pins
from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRef
from pipe_inspector.contracts.inspection import ImageObservation
from pipe_inspector.contracts.intake import TaskRecord, TaskSubmission
from pipe_inspector.contracts.planning import ActionKind, TaskPlan
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.video import (
    FrameObservation,
    VideoAggregationConfig,
    VideoFrameToolPin,
)
from pipe_inspector.contracts.video_selection import VIDEO_SELECTION_VERSION
from pipe_inspector.runtime_settings import current_api_settings, revision_pin
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.vision.detector import UltralyticsDetector

POLICY_VERSION = RECALL_POLICY_VERSION
PROMPT_VERSION = DISCOVERY_PROMPT_VERSION
DETECT_TIMEOUT = 180


def run_config(
    settings: Settings,
    detector=None,
    *,
    force_review: bool = False,
    plan: TaskPlan | None = None,
    inspection_mode: str = "yolo_vlm",
) -> dict:
    settings = current_api_settings(settings)
    if inspection_mode not in {"yolo_only", "yolo_vlm"}:
        raise InspectionStartError("inspection_mode_invalid", "不支持的检查模式", 409)
    _validate_inspection_mode_plan(inspection_mode, plan)
    if inspection_mode == "yolo_vlm" and settings.vlm_base_url:
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
    _reject_retired_plan(plan)
    config = {
        **revision_pin(settings),
        "detector_id": detector.detector_id,
        "model_version": detector.model_version,
        "label_space_version": detector.label_space_version,
        "inspection_mode": inspection_mode,
        "human_review_version": "human-review-v1",
        "detect_call_id": str(uuid4()),
        "gpu_queue": settings.task_queue + "-gpu",
        "force_review": force_review if inspection_mode == "yolo_vlm" else False,
        "plan": plan.model_dump(mode="json") if plan is not None else None,
    }
    if inspection_mode == "yolo_only":
        config["detector_only_policy_version"] = DETECTOR_ONLY_POLICY_VERSION
    else:
        config.update(
            vlm_model=settings.vlm_model,
            vlm_base_url=settings.vlm_base_url,
            **review_pins(settings.image_review_mode),
            api_timeout_seconds=settings.api_timeout_seconds,
            api_enable_thinking=settings.api_enable_thinking,
            api_max_output_tokens=settings.api_max_output_tokens,
            max_image_bytes=settings.vlm_max_image_bytes,
            verify_call_id=str(uuid4()),
        )
    if plan and any(s.action == ActionKind.ENHANCE for s in plan.steps):
        from pipe_inspector.workflow.enhancement_config import (
            build_auto_enhancement_config,
            build_enhancement_config,
        )

        if plan.auto_enhancement:
            config["auto_enhancement"] = build_auto_enhancement_config(settings)
        elif plan.enhancement is None:
            raise InspectionStartError("enhancement_parameters_required", "请指定增强种类", 409)
        else:
            config["enhancement"] = build_enhancement_config(settings, plan.enhancement)
    if plan and any(
        s.action in {ActionKind.SEGMENT, ActionKind.RELATIVE_DEPTH, ActionKind.RELATIVE_MEASURE}
        for s in plan.steps
    ):
        from pipe_inspector.workflow.geometry_config import build_geometry_config

        if plan.geometry is None:
            raise InspectionStartError(
                "geometry_parameters_required", "请明确几何工具及原图区域", 409
            )
        config["geometry"] = build_geometry_config(settings, plan.geometry)
    return config


def _validate_inspection_mode_plan(inspection_mode: str, plan: TaskPlan | None) -> None:
    if (
        inspection_mode == "yolo_only"
        and plan
        and (
            plan.enhancement is not None
            or plan.auto_enhancement
            or plan.geometry is not None
            or any(
                step.action
                in {
                    ActionKind.ENHANCE,
                    ActionKind.SEGMENT,
                    ActionKind.RELATIVE_DEPTH,
                    ActionKind.RELATIVE_MEASURE,
                }
                for step in plan.steps
            )
        )
    ):
        raise InspectionStartError(
            "inspection_mode_tool_unsupported",
            "纯 YOLO 模式不支持增强或几何工具；请新建 YOLO＋VLM 图片检查",
            409,
        )


def video_run_config(
    settings: Settings,
    detector=None,
    *,
    force_review: bool = False,
    plan: TaskPlan | None = None,
) -> dict:
    if plan and (
        plan.geometry is not None
        or any(
            s.action in {ActionKind.SEGMENT, ActionKind.RELATIVE_DEPTH, ActionKind.RELATIVE_MEASURE}
            for s in plan.steps
        )
    ):
        raise InspectionStartError("geometry_image_only", "视频几何工具尚未接入", 409)
    if plan and any(s.action == ActionKind.ENHANCE for s in plan.steps):
        raise InspectionStartError("enhancement_image_only", "视频增强尚未接入", 409)
    config = run_config(settings, detector=detector, force_review=force_review, plan=plan) | {
        "operation": "video_inspection",
        "policy_version": "image-policy-v1",
        "prompt_version": IMAGE_REVIEW_PROMPT_VERSION,
        "view_policy_version": None,
        "image_review_mode": "candidate_review",
        "video_policy_version": "video-review-v1",
        "video_selection_version": VIDEO_SELECTION_VERSION,
        "video_aggregation": VideoAggregationConfig().model_dump(mode="json"),
        "video_chunk_size": 8,
        "video_review_budget": 32,
    }
    return config


class InspectionStartError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 503):
        self.code, self.status_code = code, status_code
        super().__init__(message)


def _reject_retired_plan(plan: TaskPlan | None) -> None:
    if plan and (
        plan.open_anomaly_audit or any(s.action == ActionKind.OPEN_SCAN for s in plan.steps)
    ):
        raise InspectionStartError("tool_unavailable", "开放候选补检已停用，请使用 VLM 复检", 409)


def validate_existing_admission(
    record: TaskRecord, submission: TaskSubmission, *, parent_task_id: UUID | None
) -> TaskRecord:
    stored_submission = TaskSubmission(
        request_id=record.request_id,
        session_id=record.session_id,
        input_asset=record.input_asset,
        instruction=record.instruction,
        inspection_mode=record.inspection_mode,
    )
    if stored_submission != submission or record.parent_task_id != parent_task_id:
        raise SubmissionConflict
    return record


class InspectionStarter:
    def __init__(self, settings, repository, *, connect=Client.connect):
        self.settings, self.repo, self.connect = settings, repository, connect

    async def admit_supplement(
        self,
        parent_task_id: UUID,
        submission: TaskSubmission,
        *,
        enhancement=None,
        geometry=None,
        auto_enhancement: bool = False,
    ):
        if sum((enhancement is not None, geometry is not None, auto_enhancement)) > 1:
            raise InspectionStartError("geometry_combination_invalid", "几何与增强请分开请求", 409)
        if enhancement is not None or geometry is not None or auto_enhancement:
            existing = await asyncio.to_thread(
                self.repo.find_task_by_request_id, submission.request_id
            )
            if existing is not None:
                return validate_existing_admission(
                    existing, submission, parent_task_id=parent_task_id
                )
        if getattr(submission, "inspection_mode", "yolo_vlm") == "yolo_only":
            raise InspectionStartError(
                "inspection_mode_tool_unsupported",
                "纯 YOLO 任务不支持追加复检、增强或几何工具",
                409,
            )
        get_task = getattr(self.repo, "get_task", None)
        if get_task is not None:
            parent = await asyncio.to_thread(get_task, parent_task_id)
            if parent.inspection_mode == "yolo_only":
                raise InspectionStartError(
                    "inspection_mode_tool_unsupported",
                    "纯 YOLO 任务不支持追加复检、增强或几何工具",
                    409,
                )
            if submission.inspection_mode != parent.inspection_mode:
                raise InspectionStartError(
                    "inspection_mode_conflict", "追加任务不能改变原任务的检查模式", 409
                )
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
        if enhancement is not None:
            from pipe_inspector.workflow.enhancement_config import (
                build_enhancement_config,
                validate_enhancement_input,
            )

            asset = await asyncio.to_thread(self.repo.get_asset, submission.input_asset)
            validate_enhancement_input(asset, enhancement)
            config["enhancement"] = build_enhancement_config(self.settings, enhancement)
        if auto_enhancement:
            from pipe_inspector.workflow.enhancement_config import build_auto_enhancement_config

            asset = await asyncio.to_thread(self.repo.get_asset, submission.input_asset)
            if asset.kind != AssetKind.IMAGE or asset.parent is not None:
                raise InspectionStartError(
                    "enhancement_image_only", "当前增强只支持原始图片任务", 409
                )
            config["auto_enhancement"] = build_auto_enhancement_config(self.settings)
        if geometry is not None:
            from pipe_inspector.workflow.geometry_config import (
                build_geometry_config,
                validate_geometry_input,
            )

            asset = await asyncio.to_thread(self.repo.get_asset, submission.input_asset)
            parent = await asyncio.to_thread(self.repo.get_task, parent_task_id)
            validate_geometry_input(asset, geometry, parent)
            config["geometry"] = build_geometry_config(self.settings, geometry)
        return await asyncio.to_thread(
            self.repo.admit_supplement, parent_task_id, submission, config
        )

    async def admit_frame_tool(
        self,
        parent_task_id: UUID,
        submission: TaskSubmission,
        *,
        enhancement=None,
        geometry=None,
    ):
        if (enhancement is None) == (geometry is None):
            raise InspectionStartError(
                "frame_tool_combination_invalid",
                "请选择一种视频证据帧增强或几何操作",
                409,
            )
        if submission.inspection_mode == "yolo_only":
            raise InspectionStartError(
                "inspection_mode_tool_unsupported",
                "纯 YOLO 模式不能执行视频证据帧增强或几何工具",
                409,
            )
        existing = await asyncio.to_thread(self.repo.find_task_by_request_id, submission.request_id)
        if existing is not None:
            validate_existing_admission(existing, submission, parent_task_id=parent_task_id)
            if existing.frame_source is None:
                raise SubmissionConflict
            return existing

        parent = await asyncio.to_thread(self.repo.get_task, parent_task_id)
        frame_asset = await asyncio.to_thread(self.repo.get_asset, submission.input_asset)
        video_asset = await asyncio.to_thread(self.repo.get_asset, parent.input_asset)
        evidence = await asyncio.to_thread(self.repo.load_step, parent_task_id, "video_evidence")
        if (
            parent.session_id != submission.session_id
            or video_asset.kind is not AssetKind.VIDEO
            or video_asset.parent is not None
            or frame_asset.kind is not AssetKind.FRAME
            or frame_asset.parent != parent.input_asset
            or evidence is None
        ):
            raise SupplementNotAllowed("source_evidence_missing")
        matches = [
            item
            for item in evidence.get("frames", ())
            if AssetRef.model_validate(item.get("asset")) == submission.input_asset
        ]
        if len(matches) != 1:
            raise SupplementNotAllowed("source_evidence_missing")
        artifact = matches[0]
        try:
            observation = await asyncio.to_thread(
                self.repo.get_video_frame, parent_task_id, artifact["frame_index"]
            )
            observation = FrameObservation.model_validate(observation)
            if (
                observation.frame_index != artifact["frame_index"]
                or observation.timestamp_seconds != artifact["timestamp_seconds"]
                or (observation.detection.width, observation.detection.height)
                != (frame_asset.width, frame_asset.height)
            ):
                raise ValueError("frame evidence mismatch")
        except (KeyError, TypeError, ValueError):
            raise SupplementNotAllowed("source_evidence_missing") from None

        operation = "enhance" if enhancement is not None else geometry.operation
        frame_source = VideoFrameToolPin(
            parent_task_id=parent_task_id,
            video_asset=parent.input_asset,
            frame_asset=submission.input_asset,
            frame_index=observation.frame_index,
            timestamp_seconds=observation.timestamp_seconds,
            width=observation.detection.width,
            height=observation.detection.height,
            operation=operation,
        )
        config = run_config(self.settings, detector=observation.detection, force_review=True)
        config.update(
            operation="supplement_review",
            parent_task_id=str(parent_task_id),
            supplement_policy_version=SUPPLEMENT_POLICY_VERSION,
            frame_source=frame_source.model_dump(mode="json"),
        )
        if enhancement is not None:
            from pipe_inspector.workflow.enhancement_config import (
                build_enhancement_config,
                validate_enhancement_input,
            )

            validate_enhancement_input(frame_asset, enhancement, allow_frame=True)
            config["enhancement"] = build_enhancement_config(self.settings, enhancement)
        else:
            from pipe_inspector.workflow.geometry_config import (
                build_geometry_config,
                validate_geometry_input,
            )

            validate_geometry_input(frame_asset, geometry, allow_frame=True)
            config["geometry"] = build_geometry_config(self.settings, geometry)
        return await asyncio.to_thread(
            self.repo.admit_frame_tool, parent_task_id, submission, config
        )

    async def start(
        self, task_id: UUID, *, force_review: bool = False, plan: TaskPlan | None = None
    ):
        _reject_retired_plan(plan)
        record = await asyncio.to_thread(self.repo.get_task, task_id)
        asset = await asyncio.to_thread(self.repo.get_asset, record.input_asset)
        run = None
        is_frame_tool = asset.kind is AssetKind.FRAME
        if is_frame_tool:
            run = await asyncio.to_thread(self.repo.get_run, task_id)
            try:
                from pipe_inspector.workflow.frame_tools import validate_frame_tool_context

                validate_frame_tool_context(self.repo, run, record, asset)
            except (KeyError, TypeError, ValueError, AttributeError):
                raise InspectionStartError(
                    "unsupported_media", "视频帧缺少完整的已固定工具来源", 409
                ) from None
        if asset.kind not in {AssetKind.IMAGE, AssetKind.VIDEO} and not is_frame_tool:
            raise InspectionStartError(
                "unsupported_media", "当前执行器只支持已接纳的图片或视频", 409
            )
        is_video = asset.kind == AssetKind.VIDEO
        inspection_mode = getattr(record, "inspection_mode", "yolo_vlm")
        if inspection_mode == "yolo_only" and is_video:
            raise InspectionStartError(
                "inspection_mode_image_only", "纯 YOLO 检查当前仅支持图片", 409
            )
        _validate_inspection_mode_plan(inspection_mode, plan)
        if plan and plan.geometry is not None:
            from pipe_inspector.workflow.geometry_config import validate_geometry_input

            validate_geometry_input(asset, plan.geometry)
        if plan and plan.auto_enhancement and (asset.kind != "image" or asset.parent is not None):
            raise InspectionStartError("enhancement_image_only", "自动增强只支持原始图片", 409)
        if plan and plan.enhancement is not None:
            from pipe_inspector.workflow.enhancement_config import validate_enhancement_input

            validate_enhancement_input(asset, plan.enhancement)
        if record.snapshot.is_terminal and plan is None:
            return record
        if run is None:
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
                    (
                        video_run_config(self.settings, force_review=force_review, plan=plan)
                        if is_video
                        else run_config(
                            self.settings,
                            force_review=force_review,
                            plan=plan,
                            inspection_mode=inspection_mode,
                        )
                    ),
                )
            except SubmissionConflict:
                # A concurrent start won admission; UUID call IDs/config are already pinned.
                run = await asyncio.to_thread(self.repo.get_run, task_id)
                if run is None:
                    raise
        if run is not None and (
            (is_video and run["config"].get("operation") != "video_inspection")
            or (
                not is_video
                and parent_task_id is None
                and run["config"].get("operation") == "video_inspection"
            )
        ):
            raise InspectionStartError(
                "configuration_changed", "已固定的运行类型与任务媒体不一致", 409
            )
        if run is not None and run["config"].get("inspection_mode", "yolo_vlm") != inspection_mode:
            raise InspectionStartError(
                "configuration_changed", "已固定的检查模式与任务选择不一致", 409
            )
        if plan is not None and (
            run is None
            or (
                TaskPlan.model_validate(run["config"]["plan"])
                if run["config"].get("plan") is not None
                else None
            )
            != plan
            or run["config"].get("force_review", False)
            != (force_review if inspection_mode == "yolo_vlm" else False)
        ):
            raise InspectionStartError(
                "plan_conflict", "任务已按另一执行计划启动，不能改写；请重新发起检查", 409
            )
        if record.snapshot.is_terminal:
            return record
        if is_video and record.snapshot.stage == "queued" and not record.snapshot.can_cancel:
            cancellable = TaskSnapshot.model_validate(
                record.snapshot.model_dump()
                | {"can_cancel": True, "updated_at": datetime.now(timezone.utc)}
            )
            try:
                record = await asyncio.to_thread(self.repo.update_snapshot, cancellable)
            except SubmissionConflict:
                current = await asyncio.to_thread(self.repo.get_task, task_id)
                if current.snapshot.is_terminal:
                    return current
                raise
        try:
            async with asyncio.timeout(10):
                client = await self.connect(
                    self.settings.temporal_address, namespace=self.settings.temporal_namespace
                )
                await client.start_workflow(
                    (
                        "ImageSupplementWorkflow"
                        if parent_task_id
                        else "VideoInspectionWorkflow"
                        if is_video
                        else "ImageInspectionWorkflow"
                    ),
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

    async def cancel(self, task_id: UUID):
        record = await asyncio.to_thread(self.repo.get_task, task_id)
        asset = await asyncio.to_thread(self.repo.get_asset, record.input_asset)
        run = await asyncio.to_thread(self.repo.get_run, task_id)
        review_enabled = run and run["config"].get("human_review_version") == "human-review-v1"
        if asset.kind != AssetKind.VIDEO and not (
            review_enabled
            and (
                record.snapshot.runtime_status == "waiting_review"
                or record.snapshot.runtime_status == "cancelled"
            )
        ):
            raise InspectionStartError("video_only", "图片任务仅在人工审核等待期间支持取消", 409)
        if record.snapshot.is_terminal:
            return record
        if run is None:
            raise InspectionStartError("not_started", "任务尚未启动，无法取消", 409)
        try:
            async with asyncio.timeout(10):
                client = await self.connect(
                    self.settings.temporal_address, namespace=self.settings.temporal_namespace
                )
                handle = client.get_workflow_handle(f"inspection:{task_id}")
                accepted = await handle.execute_update("request_cancel")
        except (
            RPCError,
            OSError,
            TimeoutError,
            RuntimeError,
            WorkflowUpdateFailedError,
            WorkflowUpdateRPCTimeoutOrCancelledError,
        ):
            raise InspectionStartError(
                "workflow_unavailable", "取消请求暂未确认，请重试；任务不会被重新启动"
            ) from None
        if not accepted:
            raise InspectionStartError("finalizing", "任务正在提交最终结果，已不能取消", 409)
        return await asyncio.to_thread(self.repo.get_task, task_id)
