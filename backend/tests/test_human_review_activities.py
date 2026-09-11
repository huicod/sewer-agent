from types import SimpleNamespace
from uuid import UUID

import pytest
from pipe_inspector.agent.human_review import HumanReviewConflict
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_human_review import review, submission, waiting_snapshot


class Store:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.steps = {}
        self.body = None

    def apply_human_review(self, task_id, body):
        from pipe_inspector.agent.human_review import resolve_human_review

        assert task_id == self.snapshot.task_id
        self.body = body
        self.snapshot = resolve_human_review(self.snapshot, body, now=self.snapshot.updated_at)
        return SimpleNamespace(task_id=task_id, submission=body, after=self.snapshot)

    def get_task(self, task_id):
        return SimpleNamespace(snapshot=self.snapshot)

    def load_step(self, task_id, key):
        return self.steps.get(key)

    def save_step(self, task_id, key, value):
        return self.steps.setdefault(key, value)

    def update_snapshot(self, snapshot):
        self.snapshot = snapshot


def test_review_activity_returns_only_compact_receipt():
    from pipe_inspector.workflow.human_review_activities import HumanReviewActivities

    snapshot = waiting_snapshot()
    store = Store(snapshot)
    body = submission(snapshot, review(snapshot.findings[0], "confirm"))
    result = ActivityEnvironment().run(
        HumanReviewActivities(store).apply_human_review,
        {"task_id": str(snapshot.task_id), "submission": body.model_dump(mode="json")},
    )
    assert set(result) == {"task_id", "request_id", "runtime_status", "inspection_result"}
    assert UUID(result["request_id"]) == body.request_id
    assert store.body == body


def test_review_activity_domain_conflict_is_safe_nonretryable():
    from pipe_inspector.workflow.human_review_activities import HumanReviewActivities

    snapshot = waiting_snapshot()
    body = submission(snapshot, review(snapshot.findings[0], "confirm"))
    store = Store(snapshot)

    def conflict(*args):
        raise HumanReviewConflict("review_conflict", "private evidence must not escape")

    store.apply_human_review = conflict
    with pytest.raises(ApplicationError) as raised:
        ActivityEnvironment().run(
            HumanReviewActivities(store).apply_human_review,
            {
                "task_id": str(snapshot.task_id),
                "submission": body.model_dump(mode="json"),
            },
        )
    assert raised.value.type == "review_conflict"
    assert raised.value.non_retryable
    assert "private" not in str(raised.value)


def test_cancel_waiting_review_is_durable_and_cannot_overwrite_terminal_result():
    from pipe_inspector.workflow.human_review_activities import HumanReviewActivities

    snapshot = waiting_snapshot()
    store = Store(snapshot)
    activities = HumanReviewActivities(store)
    payload = {"task_id": str(snapshot.task_id)}
    first = ActivityEnvironment().run(activities.cancel_human_review, payload)
    assert first["runtime_status"] == "cancelled"
    assert store.snapshot.findings == snapshot.findings
    assert not store.snapshot.can_cancel
    assert "human_review_cancel" in store.steps
    assert ActivityEnvironment().run(activities.cancel_human_review, payload) == first
