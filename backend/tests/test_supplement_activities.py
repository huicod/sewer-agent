from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pipe_inspector.config import Settings
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.start import run_config
from pipe_inspector.workflow.supplement_activities import SupplementActivities
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from test_image_activities import Store
from test_supplement_policy import observation, previous, review


def test_admitted_supplement_policy_matches_starter_and_rejects_old_version():
    from pipe_inspector.agent.supplement_policy import SUPPLEMENT_POLICY_VERSION
    from pipe_inspector.storage.repository import SubmissionConflict
    from pipe_inspector.storage.supplements import SupplementRepository

    observed = observation(False)
    parent_id = uuid4()
    config = run_config(Settings(_env_file=None), observed.detection, force_review=True) | dict(
        operation="supplement_review",
        parent_task_id=str(parent_id),
        supplement_policy_version=SUPPLEMENT_POLICY_VERSION,
    )
    parent_run = observed.detection.model_dump(mode="json")
    SupplementRepository._validate_config(config, parent_id, parent_run, observed)
    with pytest.raises(SubmissionConflict):
        SupplementRepository._validate_config(
            config | dict(supplement_policy_version="supplement-policy-v1"),
            parent_id,
            parent_run,
            observed,
        )


@pytest.mark.parametrize("configuration_changed", [False, True])
async def test_supplement_finalization_preserves_frozen_source_and_replays_without_api(
    configuration_changed,
):
    store = Store()
    observed = observation(False)
    old_review = review(
        additional=[{"label": "OTHER", "name": "疑似异物", "reason": "visible anomaly"}]
    )
    parent = TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=store.task.input_asset,
        instruction="原检查",
        created_at=store.task.snapshot.updated_at,
        snapshot=previous(store.task.input_asset, observed, old_review),
    )
    store.task.parent_task_id = parent.snapshot.task_id
    store.steps = {
        "detect": observed.model_dump(mode="json"),
        "source": {
            "task": parent.model_dump(mode="json"),
            "verify": {"review": old_review.model_dump(mode="json"), "skipped": False},
        },
    }
    settings = Settings(_env_file=None)
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, observed.detection, force_review=True)
        | {
            "operation": "supplement_review",
            "parent_task_id": str(parent.snapshot.task_id),
            "supplement_policy_version": "supplement-policy-v2",
        },
    }
    reviewer = SimpleNamespace(review=AsyncMock(return_value=review()))
    detector = SimpleNamespace(predict_image=Mock(side_effect=AssertionError("must not detect")))
    activities = SupplementActivities(
        settings,
        store,
        SimpleNamespace(path_for=lambda _: "fixture.png"),
        detector=detector,
        reviewer=reviewer,
    )
    env = ActivityEnvironment()
    await env.run(activities.verify_image, run)
    if configuration_changed:
        run["config"]["supplement_policy_version"] = "retired-policy"
    first = env.run(activities.finish_supplement, {"run": run})
    assert first["runtime_status"] == ("failed" if configuration_changed else "waiting_review")
    assert first["inspection_result"] == "inconclusive"
    assert first["findings"][0]["canonical_label"] == "OTHER"
    assert store.steps["source"]["task"] == parent.model_dump(mode="json")
    await env.run(activities.verify_image, run)
    assert env.run(activities.finish_supplement, {"run": run}) == first
    reviewer.review.assert_awaited_once()
    detector.predict_image.assert_not_called()
    assert [call.tool_name for call in store.calls] == ["verify"]


async def test_shared_production_verifier_checks_child_policy_before_api():
    store = Store()
    observed = observation(False)
    store.steps["detect"] = observed.model_dump(mode="json")
    settings = Settings(_env_file=None)
    reviewer = SimpleNamespace(review=AsyncMock(return_value=review()))
    activities = ImageActivities(
        settings, store, SimpleNamespace(path_for=lambda _: "fixture.png"), reviewer=reviewer
    )
    run = {
        "task_id": str(store.task_id),
        "run_id": str(uuid4()),
        "config": run_config(settings, observed.detection, force_review=True)
        | {
            "operation": "supplement_review",
            "supplement_policy_version": "retired-policy",
        },
    }
    with pytest.raises(ApplicationError) as error:
        await ActivityEnvironment().run(activities.verify_image, run)
    assert error.value.type == "configuration_changed"
    reviewer.review.assert_not_awaited()
