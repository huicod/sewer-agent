"""A bounded comparison action over frozen, session-owned inspection evidence."""

import asyncio
import re
from uuid import UUID

from pipe_inspector.comparison import ComparisonNotAllowed
from pipe_inspector.contracts.comparison import ComparisonResult
from pipe_inspector.reports import LABELS

_UUID_TOKEN = re.compile(
    r"(?<![0-9a-zA-Z_])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-zA-Z_])"
)
_ERRORS = {
    "comparison_same_task": "请选择两个不同的检查任务。",
    "comparison_source_not_ready": "请等待两项检查结束；仅支持 completed 或 partial 的已存结果。",
    "comparison_media_mismatch": "比较来源的媒体类型必须一致：图片与图片、视频与视频。",
    "comparison_original_only": "比较只能引用基于原始图片或视频的检查结果。",
}


def _reply(text, task_id=None):
    return {"reply": text, "linked_task_id": str(task_id) if task_id else None}


async def compare_saved_tasks(repo, turn, intent, current, *, attempt=1):
    if turn.input_asset is not None:
        return _reply("新上传媒体尚无检查结果，请先完成检查，再选择两项已有任务比较。")
    parsed_reference = intent.comparison_reference_task_id
    if (turn.reference_task_id is not None and parsed_reference is not None
            and turn.reference_task_id != parsed_reference):
        return _reply("文本中的参考任务与界面选择不一致，请明确要比较的参考任务。")
    if parsed_reference is not None and turn.reference_task_id is None:
        tokens = {UUID(match.group()) for match in _UUID_TOKEN.finditer(turn.text)}
        if parsed_reference not in tokens:
            return _reply("请在本条文本明确给出参考任务 ID，或在界面选择；不能猜测引用。")
    reference_id = turn.reference_task_id or parsed_reference
    if reference_id is None:
        return _reply("请选择比较参考任务，或在本条文本提供其任务 ID；不会重跑检查模型。")
    if current is None:
        return _reply("请选择当前检查任务，再与参考任务比较；目前没有可引用的当前证据。")
    try:
        raw = await asyncio.to_thread(
            repo.save_comparison, turn.request_id, reference_id, current.snapshot.task_id,
            attempt=attempt,
        )
    except ComparisonNotAllowed as exc:
        return _reply(_ERRORS[exc.code] + "本次未执行比较。")
    result = ComparisonResult.model_validate(raw)
    lines = [
        "已保存比较：仅对照两份已存检查证据，未重跑检测或 VLM。",
        f"参考任务：{result.reference.snapshot.task_id}。",
        f"当前任务：{result.current.snapshot.task_id}。",
        "逐类已确认数量（参考 → 当前）：",
    ]
    for row in result.rows:
        label = LABELS.get(row.label, "待定类别")
        lines.append(f"{row.label}（{label}）：{row.reference.confirmed} → "
                     f"{row.current.confirmed}（差值 {row.confirmed_delta:+d}）。")
    lines.append(
        "待定、待人工复核、已驳回分别列于比较面板与 JSON/HTML 报告。"
        "拍摄位置匹配未确认；数量差异不代表真实新增、消失或维修效果，不能据此判断正常。"
    )
    return _reply("\n".join(lines), result.current.snapshot.task_id)
