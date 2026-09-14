"""Constrained conversation actions over existing evidence and media workflows."""

import asyncio

from pipe_inspector.agent.intent import IntentPlanningError, compile_inspection_plan
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.planning import ActionKind
from pipe_inspector.reports import LABELS, RESULTS, REVIEWS
from pipe_inspector.storage.repository import MissingRecord
from pipe_inspector.storage.supplements import SupplementNotAllowed
from pipe_inspector.workflow.enhancement_config import (
    build_enhancement_config,
    validate_enhancement_input,
)
from pipe_inspector.workflow.start import InspectionStartError, validate_existing_admission

AVAILABLE_ACTIONS = frozenset(
    {
        ActionKind.DETECT,
        ActionKind.VERIFY,
        ActionKind.ENHANCE,
        ActionKind.SEGMENT,
        ActionKind.RELATIVE_DEPTH,
        ActionKind.RELATIVE_MEASURE,
        ActionKind.SUMMARIZE,
        ActionKind.EXPORT_REPORT,
    }
)


class ConversationDispatcher:
    def __init__(self, repository, starter):
        self.repo, self.starter = repository, starter

    async def _target(self, turn):
        task_id = turn.task_id
        if task_id is None:
            history = await asyncio.to_thread(self.repo.list_turns, turn.session_id)
            previous = [
                item
                for item in history
                if item.request_id != turn.request_id
                and item.created_at < turn.created_at
                and item.status == "completed"
                and item.linked_task_id
            ]
            if previous:
                task_id = previous[-1].linked_task_id
        if task_id is None:
            return None
        task = await asyncio.to_thread(self.repo.get_task, task_id)
        if task.session_id != turn.session_id:
            raise MissingRecord
        return task

    @staticmethod
    def _reply(text, task_id=None):
        return {"reply": text, "linked_task_id": str(task_id) if task_id else None}

    @staticmethod
    def _enhancement_error(exc):
        if exc.code == "enhancement_image_only":
            return "当前只支持原始图片增强，视频或派生图片增强尚未接入。"
        return str(exc) + "；本次未创建任务或执行工具。"

    def _preflight_enhancement(self, asset, options):
        validate_enhancement_input(asset, options)
        build_enhancement_config(self.starter.settings, options)

    def _preflight_geometry(self, asset, options, task=None):
        from pipe_inspector.workflow.geometry_config import (
            build_geometry_config,
            validate_geometry_input,
        )
        validate_geometry_input(asset, options, task)
        build_geometry_config(self.starter.settings, options)

    async def handle(self, turn, intent, *, attempt=1):
        if intent.kind in {"clarify", "refuse"}:
            return self._reply(intent.message)
        if turn.reference_task_id is not None and intent.kind != "compare":
            return self._reply(
                "已选择比较参考任务，但本条没有明确解析为比较请求；本次未执行工具。"
                "请明确比较两份检查，或清除比较参考后发送其他操作。"
            )
        if intent.kind == "compare":
            from pipe_inspector.agent.comparison import compare_saved_tasks
            from pipe_inspector.contracts.comparison import ComparisonResult

            cached = await asyncio.to_thread(
                self.repo.load_turn_step, turn.request_id, "comparison"
            )
            task = (ComparisonResult.model_validate(cached).current
                    if cached is not None else await self._target(turn))
            return await compare_saved_tasks(self.repo, turn, intent, task, attempt=attempt)
        task = await self._target(turn)
        if intent.kind == "supplement":
            return await self._supplement(turn, intent, task)
        if intent.kind == "inspect":
            try:
                plan = compile_inspection_plan(intent, available_actions=AVAILABLE_ACTIONS)
            except IntentPlanningError as exc:
                if exc.code.startswith("geometry_"):
                    return self._reply(
                        "请明确分割、整图相对深度或相对测量；分割需要提供原图像素区域"
                        " [x1,y1,x2,y2]。已有 finding ID 请通过追加操作引用；本次未启动。"
                    )
                if exc.code == "enhancement_parameters_required":
                    return self._reply(
                        "请从暗光恢复、去雾、去模糊或局部超分中明确选择一种增强；"
                        "局部超分还需提供原图像素框。本次未启动检查。"
                    )
                return self._reply(
                    "所请求的工具当前未部署或已停用，本次未启动检查。"
                    "当前可用：图片/视频五类检测、条件 VLM 复检、"
                    "显式或允许后按需图片增强、分割/相对深度与证据报告。"
                )
            if plan.geometry is not None and turn.input_asset is None and task is not None:
                return self._reply(
                    "已选择已有任务，请明确请求关联几何操作，以复用冻结证据和补证额度；"
                    "若确需新建检查，请本条重新上传或指定原图。本次未启动。",
                    task.snapshot.task_id,
                )
            asset_ref = turn.input_asset or (task.input_asset if task else None)
            if asset_ref is None:
                return self._reply("请上传 CCTV 图片或视频，或选择一个已有任务。")
            submission = TaskSubmission(
                request_id=turn.request_id,
                session_id=turn.session_id,
                input_asset=asset_ref,
                instruction=turn.text,
            )
            existing = None
            if plan.enhancement is not None or plan.auto_enhancement or plan.geometry is not None:
                existing = await asyncio.to_thread(
                    self.repo.find_task_by_request_id, turn.request_id
                )
                if existing is not None:
                    validate_existing_admission(existing, submission, parent_task_id=None)
            asset = await asyncio.to_thread(self.repo.get_asset, asset_ref)
            if plan.geometry is not None and existing is None:
                try:
                    self._preflight_geometry(asset, plan.geometry)
                except InspectionStartError as exc:
                    return self._reply(str(exc) + "；本次未创建任务或执行工具。")
            if plan.auto_enhancement and (asset.kind != "image" or asset.parent is not None):
                return self._reply(
                    "按需增强当前仅支持原始图片，视频或派生图片尚未接入；本次未启动。"
                )
            if plan.enhancement is not None and existing is None:
                try:
                    self._preflight_enhancement(asset, plan.enhancement)
                except InspectionStartError as exc:
                    return self._reply(self._enhancement_error(exc))
            await asyncio.to_thread(
                self.repo.save_turn_step, turn.request_id, "plan", plan.model_dump(mode="json")
            )
            created = existing or await asyncio.to_thread(self.repo.create_task, submission)
            await asyncio.to_thread(
                self.repo.update_turn, turn.request_id, linked_task_id=created.snapshot.task_id
            )
            await self.starter.start(
                created.snapshot.task_id,
                force_review=ActionKind.VERIFY in intent.actions,
                plan=plan,
            )
            media_name = "视频" if asset.kind == "video" else "图片"
            if existing is not None and created.snapshot.is_terminal:
                return self._reply(
                    f"已复用已有{media_name}任务和结果，未新建任务或重复执行。",
                    created.snapshot.task_id,
                )
            return self._reply(
                f"{media_name}任务已启动，检查结果尚未完成。"
                "可在关联任务中查看阶段，稍后继续询问结果或获取报告。",
                created.snapshot.task_id,
            )
        if task is None:
            return self._reply(
                "请先选择已有任务，或上传图片/视频并发起检查；目前没有可引用的检查证据。"
            )
        snapshot = task.snapshot
        if intent.kind == "report":
            message = (
                "证据报告已准备，可通过关联任务查看 JSON 或 HTML。"
                if snapshot.is_terminal
                else "任务仍在处理，最终报告尚未就绪；请先查看关联任务进度。"
            )
            return self._reply(message, snapshot.task_id)
        lines = [
            f"结果：{RESULTS[snapshot.inspection_result]}；任务状态：{snapshot.runtime_status.value}。",
            f"覆盖充分：{snapshot.coverage.sufficient_coverage}；画质充分：{snapshot.coverage.sufficient_quality}；阴性审计完成：{snapshot.coverage.negative_audit_complete}。",
        ]
        if snapshot.waiting_reason:
            lines.append("限制/待处理：" + snapshot.waiting_reason[:500])
        selected = [
            f
            for f in snapshot.findings
            if f.canonical_label in intent.target_labels or f.canonical_label in {None, "OTHER"}
        ]
        for finding in selected[:8]:
            label = LABELS.get(finding.canonical_label, "待定")
            lines.append(
                f"{finding.canonical_label or '待定'}（{label}）："
                f"{REVIEWS[finding.review_status]}；证据 {len(finding.evidence)} 项；"
                f"finding ID：{finding.finding_id}。"
            )
        if not selected:
            lines.append("关注类别中暂无已保存的候选；这不单独构成正常结论。")
        if len(selected) > 8:
            lines.append(f"共 {len(selected)} 项关注发现，此处展示前 8 项，完整内容见报告。")
        verified = await asyncio.to_thread(self.repo.load_step, snapshot.task_id, "verify")
        if verified and isinstance(verified.get("review"), dict):
            summary = verified["review"].get("summary", "")
            if isinstance(summary, str) and summary:
                lines.append("VLM 复检说明（建议，状态以上述已保存结果为准）：" + summary[:1000])
        for step in ("geometry_depth", "geometry_segment"):
            saved = await asyncio.to_thread(self.repo.load_step, snapshot.task_id, step)
            if step == "geometry_depth" and saved and saved.get("result"):
                from pipe_inspector.contracts.geometry import RelativeDepthResult

                depth = RelativeDepthResult.model_validate(saved["result"])
                if depth.input_asset != task.input_asset:
                    raise ValueError("geometry source mismatch")
                lines.append(
                    "已保存单图相对深度图（白近黑远），见几何证据面板；"
                    "无标定，不能换算真实尺寸，也不能跨图比较数值。"
                )
            if saved and saved.get("measurements"):
                from pipe_inspector.contracts.geometry import GeometryMeasurements

                measured = GeometryMeasurements.model_validate(saved["measurements"])
                if measured.input_asset != task.input_asset:
                    raise ValueError("geometry source mismatch")
                lines.append(f"待复核掩膜面积：{measured.area_pixels} 像素；"
                             f"占整幅图像 {measured.image_plane_fraction:.2%}，不是管道堵塞率。"
                             "无标定，不能换算真实尺寸；分割与相对深度图见关联任务证据面板。")
                break
        return self._reply("\n".join(lines), snapshot.task_id)

    async def _supplement(self, turn, intent, task):
        if set(intent.actions) & {ActionKind.SEGMENT, ActionKind.RELATIVE_DEPTH,
                                  ActionKind.RELATIVE_MEASURE}:
            return await self._geometry_supplement(turn, intent, task)
        allowed = {
            ActionKind.VERIFY,
            ActionKind.ENHANCE,
            ActionKind.SUMMARIZE,
            ActionKind.EXPORT_REPORT,
        }
        requested = set(intent.actions)
        if not requested & {ActionKind.VERIFY, ActionKind.ENHANCE} or not requested <= allowed:
            return self._reply(
                "当前追加操作仅支持 VLM 复检或一种图片增强及其报告；"
                "请求包含未部署工具，本次未执行任何追加操作。"
            )
        enhancement_requested = ActionKind.ENHANCE in requested
        if enhancement_requested and intent.enhancement is None and not intent.auto_enhancement:
            return self._reply(
                "请从暗光恢复、去雾、去模糊或局部超分中明确选择一种追加增强；"
                "局部超分还需提供原图像素框。本次未执行。"
            )
        if task is None:
            return self._reply("请先选择一个已完成或部分完成的图片任务，再请求追加操作。")
        parent_id = task.snapshot.task_id
        if turn.input_asset is not None and turn.input_asset != task.input_asset:
            return self._reply(
                "追加复检必须使用原任务图片；本次未执行。若要检查新图片，请发起新检查。", parent_id
            )
        submission = TaskSubmission(
            request_id=turn.request_id,
            session_id=turn.session_id,
            input_asset=task.input_asset,
            instruction=turn.text,
        )
        existing = None
        if enhancement_requested:
            existing = await asyncio.to_thread(
                self.repo.find_task_by_request_id, turn.request_id
            )
            if existing is not None:
                validate_existing_admission(
                    existing, submission, parent_task_id=parent_id
                )
        if enhancement_requested and existing is None and not intent.auto_enhancement:
            asset = await asyncio.to_thread(self.repo.get_asset, task.input_asset)
            try:
                self._preflight_enhancement(asset, intent.enhancement)
            except InspectionStartError as exc:
                return self._reply(self._enhancement_error(exc), parent_id)
        supplement_kwargs = (
            {"auto_enhancement": True}
            if intent.auto_enhancement
            else {"enhancement": intent.enhancement}
            if enhancement_requested
            else {}
        )
        try:
            child = await self.starter.admit_supplement(
                parent_id,
                submission,
                **supplement_kwargs,
            )
        except SupplementNotAllowed as exc:
            message = {
                "supplement_limit": (
                    "该原任务的追加复检次数已用完（最多一次）；"
                    "仅启动确认失败时可重试同一关联任务；已结束复检不再执行。"
                ),
                "source_not_ready": "原任务尚未完成或不处于可复检状态，请先查看原任务进度。",
                "image_only": "当前追加复检仅支持图片任务，视频复检尚未接入。",
                "source_evidence_missing": "原任务缺少有效的检测证据，无法追加复检。",
            }[exc.code]
            return self._reply(message + "本次未执行新工具，原结果保持不变。", parent_id)
        child_id = child.snapshot.task_id
        # Link before Temporal start: ambiguous network failure must not orphan the child.
        await asyncio.to_thread(self.repo.update_turn, turn.request_id, linked_task_id=child_id)
        await self.starter.start(child_id)
        if existing is not None and child.snapshot.is_terminal:
            return self._reply(
                "已复用已有关联任务和结果，未新建补证或重复执行；"
                "原任务和已固定运行参数保持不变。",
                child_id,
            )
        if intent.auto_enhancement:
            return self._reply(
                "关联按需增强已启动，复用原图片和已冻结证据，不重新运行检测器或原图复检；"
                "系统仅在当前轮许可下选择必要的增强，可在关联任务中查看进度和报告。",
                child_id,
            )
        if enhancement_requested:
            return self._reply(
                "关联增强已启动，复用原图片和已冻结证据，不重新运行检测器或修改父任务；"
                "增强图及辅助复核尚未完成，可在关联任务中查看进度和报告。",
                child_id,
            )
        return self._reply(
            "关联复检已启动，复用原图片和检测证据，不重新运行检测器；原结果保留。新的复检结果尚未完成，可在关联任务中查看进度和报告。",
            child_id,
        )

    async def _geometry_supplement(self, turn, intent, task):
        allowed = {ActionKind.SEGMENT, ActionKind.RELATIVE_DEPTH, ActionKind.RELATIVE_MEASURE,
                   ActionKind.SUMMARIZE, ActionKind.EXPORT_REPORT}
        if not set(intent.actions) <= allowed or intent.auto_enhancement:
            return self._reply("本次请只选择一种几何操作包，不与增强或追加VLM混用；未执行工具。")
        if intent.geometry is None:
            return self._reply("请明确几何操作；分割/相对测量需提供原图区域或已有 finding ID。")
        if task is None:
            return self._reply("请先选择一个已完成或部分完成的原始图片任务。")
        parent_id = task.snapshot.task_id
        if turn.input_asset is not None and turn.input_asset != task.input_asset:
            return self._reply("追加几何工具必须使用原任务图片；本次未执行。", parent_id)
        submission = TaskSubmission(request_id=turn.request_id, session_id=turn.session_id,
            input_asset=task.input_asset, instruction=turn.text)
        existing = await asyncio.to_thread(self.repo.find_task_by_request_id, turn.request_id)
        if existing is not None:
            validate_existing_admission(existing, submission, parent_task_id=parent_id)
        else:
            asset = await asyncio.to_thread(self.repo.get_asset, task.input_asset)
            try:
                self._preflight_geometry(asset, intent.geometry, task)
            except InspectionStartError as exc:
                return self._reply(str(exc) + "；本次未执行工具。", parent_id)
        try:
            child = existing or await self.starter.admit_supplement(
                parent_id, submission, geometry=intent.geometry)
        except SupplementNotAllowed as exc:
            reasons = {
                "supplement_limit": "原任务的一次关联补证额度已使用，不能再次追加。",
                "source_not_ready": "原任务尚未完成或不处于可追加状态。",
                "image_only": "几何工具当前仅支持原始图片任务。",
                "source_evidence_missing": "原任务缺少有效的已保存检测证据。",
            }
            return self._reply(reasons[exc.code] + "本次未执行工具，原结果保留。", parent_id)
        child_id = child.snapshot.task_id
        await asyncio.to_thread(self.repo.update_turn, turn.request_id, linked_task_id=child_id)
        await self.starter.start(child_id)
        if existing is not None and child.snapshot.is_terminal:
            return self._reply("已复用已完成的几何结果，没有重复执行。", child_id)
        return self._reply("关联几何工具已启动，复用冻结原图证据，不重跑检测器或VLM；"
                           "像素/相对结果尚未完成，可在任务中查看进度和证据。", child_id)
