"""Finalize a linked re-review against the frozen source, not mutable/latest evidence."""

from uuid import uuid4

from temporalio import activity

from pipe_inspector.agent.supplement_policy import decide_supplement
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.workflow.activities import ImageActivities


class SupplementActivities(ImageActivities):
    def _configuration_changed_findings(self, task_id, asset, observation):
        findings = super()._configuration_changed_findings(task_id, asset, observation)
        source = self.repo.load_step(task_id, "source")
        parent = TaskRecord.model_validate(source["task"])
        return findings + tuple(
            Finding.model_validate(old.model_dump() | {"finding_id": uuid4(), "task_id": task_id})
            for old in parent.snapshot.findings
            if old.source != "detector" and old.review_status != "rejected"
        )

    def _decide(self, run, task, observation, review, error):
        source = self.repo.load_step(task.snapshot.task_id, "source")
        parent = TaskRecord.model_validate(source["task"])
        prior = source.get("verify")
        prior_review = (
            ImageReview.model_validate(prior["review"]) if prior and prior.get("review") else None
        )
        return decide_supplement(
            task.snapshot.task_id,
            task.input_asset,
            observation,
            review,
            previous=parent.snapshot,
            previous_review=prior_review,
            review_error=error,
        )

    @activity.defn(name="finish_image_supplement")
    def finish_supplement(self, payload: dict) -> dict:
        return self.finish_image(payload)
