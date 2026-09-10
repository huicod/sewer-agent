"""Constrained conversation actions over existing evidence and image workflows."""

import asyncio

from pipe_inspector.agent.intent import IntentPlanningError, compile_inspection_plan
from pipe_inspector.contracts.intake import TaskSubmission
from pipe_inspector.contracts.planning import ActionKind
from pipe_inspector.reports import LABELS, RESULTS, REVIEWS
from pipe_inspector.storage.repository import MissingRecord

AVAILABLE_ACTIONS = frozenset(
    {ActionKind.DETECT, ActionKind.VERIFY, ActionKind.SUMMARIZE, ActionKind.EXPORT_REPORT}
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

    async def handle(self, turn, intent):
        if intent.kind in {"clarify", "refuse"}:
            return self._reply(intent.message)
        if intent.kind == "supplement":
            return self._reply("关联补充复检尚未接入，本次没有执行新工具，也没有改写原结果。")
        task = await self._target(turn)
        if intent.kind == "inspect":
            try:
                plan = compile_inspection_plan(intent, available_actions=AVAILABLE_ACTIONS)
            except IntentPlanningError:
                return self._reply(
                    "所请求的工具当前未部署，本次未启动检查。"
                    "当前可用：图片五类检测、条件 VLM 复检、证据报告。"
                )
            asset_ref = turn.input_asset or (task.input_asset if task else None)
            if asset_ref is None:
                return self._reply("请上传 CCTV 图片，或选择一个已有图片任务。")
            asset = await asyncio.to_thread(self.repo.get_asset, asset_ref)
            if asset.kind != "image":
                return self._reply("视频可以上传保存，但视频检查执行器尚未接入，本次未启动检测。")
            await asyncio.to_thread(
                self.repo.save_turn_step, turn.request_id, "plan", plan.model_dump(mode="json")
            )
            created = await asyncio.to_thread(
                self.repo.create_task,
                TaskSubmission(
                    request_id=turn.request_id,
                    session_id=turn.session_id,
                    input_asset=asset_ref,
                    instruction=turn.text,
                ),
            )
            await asyncio.to_thread(
                self.repo.update_turn, turn.request_id, linked_task_id=created.snapshot.task_id
            )
            await self.starter.start(
                created.snapshot.task_id,
                force_review=ActionKind.VERIFY in intent.actions,
                plan=plan,
            )
            return self._reply(
                "图片任务已启动，检查结果尚未完成。可在关联任务中查看阶段，稍后继续询问结果或获取报告。",
                created.snapshot.task_id,
            )
        if task is None:
            return self._reply("请先选择已有任务，或上传图片并发起检查；目前没有可引用的检查证据。")
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
                f"{REVIEWS[finding.review_status]}；证据 {len(finding.evidence)} 项。"
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
        return self._reply("\n".join(lines), snapshot.task_id)
