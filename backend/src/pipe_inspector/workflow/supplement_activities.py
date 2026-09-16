"""Finalize a linked re-review against the frozen source, not mutable/latest evidence."""

from uuid import uuid4

from temporalio import activity

from pipe_inspector.agent.review_pipeline import decide_for_run, parse_image_review
from pipe_inspector.agent.supplement_policy import decide_supplement
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.workflow.activities import ImageActivities


class SupplementActivities(ImageActivities):
    def _configuration_changed_findings(self, task_id, asset, observation, run):
        if "frame_source" in run["config"]:
            from pipe_inspector.workflow.frame_tools import frozen_frame_context

            task = self.repo.get_task(task_id)
            return frozen_frame_context(self.repo, run, task)[2].findings
        findings = super()._configuration_changed_findings(task_id, asset, observation, run)
        source = self.repo.load_step(task_id, "source")
        parent = TaskRecord.model_validate(source["task"])
        return findings + tuple(
            Finding.model_validate(old.model_dump() | {"finding_id": uuid4(), "task_id": task_id})
            for old in parent.snapshot.findings
            if old.source != "detector" and old.review_status != "rejected"
        )

    def _decide(self, run, task, observation, review, error):
        if "frame_source" in run["config"]:
            from pipe_inspector.workflow.frame_tools import frozen_frame_context

            return frozen_frame_context(self.repo, run, task)[2]
        if "auto_enhancement" in run["config"] and "enhancement" not in run["config"]:
            from pipe_inspector.agent.enhancement_fusion import apply_enhancement_review
            from pipe_inspector.workflow.enhancement_activities import enhancement_baseline

            baseline = enhancement_baseline(self.repo, run, task, observation, review)
            return (
                apply_enhancement_review(baseline, None, None, error=error) if error else baseline
            )
        source = self.repo.load_step(task.snapshot.task_id, "source")
        parent = TaskRecord.model_validate(source["task"])
        prior = source.get("verify")
        prior_review = (
            parse_image_review(prior["review"]) if prior and prior.get("review") else None
        )
        return decide_supplement(
            task.snapshot.task_id,
            task.input_asset,
            observation,
            review,
            previous=parent.snapshot,
            previous_review=prior_review,
            review_error=error,
            fresh_decision=decide_for_run(
                run["config"],
                task.snapshot.task_id,
                task.input_asset,
                observation,
                review,
                review_error=error,
            ),
        )

    @activity.defn(name="finish_image_supplement")
    def finish_supplement(self, payload: dict) -> dict:
        return self.finish_image(payload)
