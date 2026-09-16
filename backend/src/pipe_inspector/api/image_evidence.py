"""Project saved image checkpoints; never invoke models or rerun fusion."""

from pipe_inspector.contracts.image_evidence import ImageEvidence
from pipe_inspector.contracts.intake import TaskRecord


def load_image_evidence(repository, task: TaskRecord) -> ImageEvidence:
    task_id = task.snapshot.task_id
    observation = repository.load_step(task_id, "detect")
    verification = repository.load_step(task_id, "verify")
    review = verification.get("review") if verification else None
    fusion = verification.get("fusion") if verification else None
    return ImageEvidence(
        task_id=task_id,
        input_asset=task.input_asset,
        parent_task_id=task.parent_task_id,
        observation=observation,
        review=review,
        fusion_changes=fusion["changes"] if fusion is not None else (),
        findings=task.snapshot.findings,
    )
