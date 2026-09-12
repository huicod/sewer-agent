"""Pure construction of saved-inspection comparisons."""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pipe_inspector.contracts.assets import AssetKind, AssetRecord
from pipe_inspector.contracts.comparison import ComparisonResult, _comparison_rows
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import RuntimeStatus

ComparisonErrorCode = Literal[
    "comparison_same_task",
    "comparison_source_not_ready",
    "comparison_media_mismatch",
    "comparison_original_only",
]


class ComparisonNotAllowed(ValueError):
    """The selected saved inspections cannot be compared."""

    def __init__(self, code: ComparisonErrorCode) -> None:
        self.code = code
        super().__init__(code)


def build_comparison(
    request_id: UUID,
    reference: TaskRecord,
    current: TaskRecord,
    reference_media: AssetRecord,
    current_media: AssetRecord,
    *,
    attempt: int = 1,
    created_at: datetime | None = None,
) -> ComparisonResult:
    """Build a point-in-time count comparison without matching individual findings."""

    if reference.snapshot.task_id == current.snapshot.task_id:
        raise ComparisonNotAllowed("comparison_same_task")
    ready = {RuntimeStatus.COMPLETED, RuntimeStatus.PARTIAL}
    if (
        reference.snapshot.runtime_status not in ready
        or current.snapshot.runtime_status not in ready
    ):
        raise ComparisonNotAllowed("comparison_source_not_ready")
    media = (reference_media, current_media)
    if any(
        asset.kind not in {AssetKind.IMAGE, AssetKind.VIDEO} or asset.parent is not None
        for asset in media
    ):
        raise ComparisonNotAllowed("comparison_original_only")
    if reference_media.kind != current_media.kind:
        raise ComparisonNotAllowed("comparison_media_mismatch")
    return ComparisonResult(
        request_id=request_id,
        created_at=created_at or datetime.now(timezone.utc),
        attempt=attempt,
        reference=reference,
        current=current,
        reference_media=reference_media,
        current_media=current_media,
        rows=_comparison_rows(reference, current),
    )
