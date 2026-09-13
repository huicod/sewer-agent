"""Provider and detector fault matrix on real local Temporal/PostgreSQL."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import torch
from pipe_inspector.agent.api_client import OpenAICompatibleClient
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.image import ImageInspectionWorkflow
from pipe_inspector.workflow.start import run_config
from temporalio.client import Client
from temporalio.worker import Worker
from test_image_workflow import EmptyDetector, case  # noqa: F401

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("SEWER_RUN_IMAGE_WORKFLOW_TEST") != "1",
        reason="opt-in local Temporal/PG fault matrix; does not restart shared services",
    ),
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
EVALUATIONS_ROOT = (REPOSITORY_ROOT / "var" / "evaluations").resolve()
CANDIDATE = {
    "label": "AJ",
    "native_label": "AJ",
    "score": 0.6,
    "bbox_xyxy": (4.0, 4.0, 40.0, 40.0),
}


@dataclass(frozen=True)
class FaultScenario:
    name: str
    responses: tuple[str, ...] = ()
    verify_statuses: tuple[str, ...] = ()
    error_code: str | None = None
    waits_for_review: bool = False
    detector_oom: bool = False


SCENARIOS = (
    FaultScenario(
        "timeout_then_success",
        responses=("timeout", "success"),
        verify_statuses=("uncertain", "succeeded"),
        error_code="timeout",
    ),
    FaultScenario(
        "rate_limit_then_success",
        responses=("rate_limited", "success"),
        verify_statuses=("failed", "succeeded"),
        error_code="rate_limited",
    ),
    FaultScenario(
        "persistent_timeout",
        responses=("timeout",),
        verify_statuses=("uncertain", "uncertain", "uncertain"),
        error_code="timeout",
        waits_for_review=True,
    ),
    FaultScenario(
        "persistent_rate_limit",
        responses=("rate_limited",),
        verify_statuses=("failed", "failed", "failed"),
        error_code="rate_limited",
        waits_for_review=True,
    ),
    FaultScenario(
        "invalid_json",
        responses=("invalid_json",),
        verify_statuses=("failed",),
        error_code="invalid_json",
        waits_for_review=True,
    ),
    FaultScenario(
        "invalid_schema",
        responses=("invalid_schema",),
        verify_statuses=("failed",),
        error_code="response_schema_invalid",
        waits_for_review=True,
    ),
    FaultScenario("detector_oom", detector_oom=True),
)


class CandidateDetector(EmptyDetector):
    def predict_image(self, path):
        batch = super().predict_image(path)
        return DetectionBatch(
            detector_id=batch.detector_id,
            model_version=batch.model_version,
            label_space_version=batch.label_space_version,
            width=batch.width,
            height=batch.height,
            detections=(Detection(**CANDIDATE),),
        )


class OOMDetector(EmptyDetector):
    def predict_image(self, path):
        super().predict_image(path)
        raise torch.OutOfMemoryError("injected detector boundary OOM")


@pytest.fixture(scope="session")
def fault_output_dir() -> Path | None:
    configured = os.getenv("SEWER_FAULT_OUTPUT")
    if not configured:
        return None
    output = Path(configured)
    if not output.is_absolute():
        output = REPOSITORY_ROOT / output
    output = output.resolve()
    if output == EVALUATIONS_ROOT or EVALUATIONS_ROOT not in output.parents:
        pytest.fail("SEWER_FAULT_OUTPUT must be a new directory beneath var/evaluations")
    if output.exists():
        pytest.fail("SEWER_FAULT_OUTPUT already exists; use a new directory for each run")
    output.mkdir(parents=True)
    return output


def _review_response(request: httpx.Request) -> httpx.Response:
    review = {
        "image_assessable": True,
        "full_image_audited": True,
        "candidates": [
            {
                "index": 0,
                "verdict": "accept",
                "proposed_label": "AJ",
                "reason": "候选区域可见侧向支管开口",
            }
        ],
        "additional_anomalies": [],
        "summary": "已复核整图并接受原始候选",
    }
    return httpx.Response(
        200,
        request=request,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(review)}}
            ]
        },
    )


def _transport(scenario: FaultScenario, attempts: list[str]) -> httpx.MockTransport:
    def provider(request: httpx.Request) -> httpx.Response:
        index = len(attempts)
        response = scenario.responses[min(index, len(scenario.responses) - 1)]
        attempts.append(response)
        if response == "timeout":
            raise httpx.ReadTimeout("injected provider timeout", request=request)
        if response == "rate_limited":
            return httpx.Response(429, request=request, json={"error": "injected"})
        if response == "invalid_json":
            return httpx.Response(
                200,
                request=request,
                content=b"{not-json",
                headers={"content-type": "application/json"},
            )
        if response == "invalid_schema":
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {
                                        "image_assessable": True,
                                        "full_image_audited": True,
                                        "candidates": "not-a-candidate-list",
                                        "additional_anomalies": [],
                                        "summary": "invalid fixture",
                                    }
                                )
                            },
                        }
                    ]
                },
            )
        assert response == "success"
        return _review_response(request)

    return httpx.MockTransport(provider)


async def _wait_for_review_projection(repo, task_id) -> dict:
    async with asyncio.timeout(30):
        while True:
            record = await asyncio.to_thread(repo.get_task, task_id)
            if str(record.snapshot.runtime_status) == "waiting_review":
                final = await asyncio.to_thread(repo.load_step, task_id, "final")
                if final is not None:
                    return final
            await asyncio.sleep(0.05)


def _write_evidence(
    output: Path | None,
    scenario: FaultScenario,
    repo,
    task_id,
    workflow_id: str,
    result: dict,
    provider_attempts: list[str],
    detector_calls: int,
) -> None:
    if output is None:
        return
    calls = repo.list_calls(task_id)
    evidence = {
        "scenario": scenario.name,
        "task": result,
        "task_id": str(task_id),
        "workflow_id": workflow_id,
        "provider_attempts": len(provider_attempts),
        "detector_attempts": detector_calls,
        "calls": [call.model_dump(mode="json") for call in calls],
        "checkpoints": {
            step: repo.load_step(task_id, step) for step in ("detect", "verify", "final")
        },
    }
    (output / f"{scenario.name}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario.name)
async def test_fault_matrix_persists_bounded_attempts_and_preserves_evidence(
    case,  # noqa: F811
    fault_output_dir,
    scenario,
):
    settings, repo, media, task = case
    settings.image_review_mode = "candidate_review"
    detector = OOMDetector() if scenario.detector_oom else CandidateDetector()
    detector.release.set()
    provider_attempts: list[str] = []
    reviewer = ImageReviewer(
        OpenAICompatibleClient(
            settings.vlm_base_url,
            settings.vlm_model,
            settings.vlm_api_key,
            timeout_seconds=settings.api_timeout_seconds,
            transport=_transport(scenario, provider_attempts)
            if not scenario.detector_oom
            else httpx.MockTransport(
                lambda request: pytest.fail("VLM must not run after detector OOM")
            ),
            enable_thinking=settings.api_enable_thinking,
            max_output_tokens=settings.api_max_output_tokens,
        ),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    activities = ImageActivities(
        settings, repo, media, detector=detector, reviewer=reviewer
    )
    task_id = task.snapshot.task_id
    run = repo.ensure_run(task_id, run_config(settings, detector))
    workflow_id = f"test-fault-matrix:{scenario.name}:{task_id}"
    assert run["config"]["image_review_mode"] == "candidate_review"
    assert run["config"]["prompt_version"] == "image-review-v2"

    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = None
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with (
                Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[ImageInspectionWorkflow],
                    activities=[
                        activities.publish_stage,
                        activities.verify_image,
                        activities.finish_image,
                    ],
                    activity_executor=executor,
                ),
                Worker(
                    client,
                    task_queue=settings.task_queue + "-gpu",
                    activities=[activities.detect_image],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ),
            ):
                handle = await client.start_workflow(
                    ImageInspectionWorkflow.run,
                    run,
                    id=workflow_id,
                    task_queue=settings.task_queue,
                )
                if scenario.waits_for_review:
                    result = await _wait_for_review_projection(repo, task_id)
                else:
                    async with asyncio.timeout(30):
                        result = await handle.result()

        calls = repo.list_calls(task_id)
        detect_calls = [call for call in calls if call.step_id == "detect"]
        verify_calls = [call for call in calls if call.step_id == "verify"]
        final = repo.load_step(task_id, "final")
        assert final == result
        assert detector.calls == 1
        assert [(call.attempt, str(call.status)) for call in detect_calls] == [
            (1, "failed" if scenario.detector_oom else "succeeded")
        ]

        if scenario.detector_oom:
            assert provider_attempts == []
            assert verify_calls == []
            assert repo.load_step(task_id, "detect") is None
            assert repo.load_step(task_id, "verify") is None
            assert result["runtime_status"] == "failed"
            assert result["inspection_result"] == "inconclusive"
            assert result["findings"] == []
            assert detect_calls[0].error.code == "detection_failed"
        else:
            detection = repo.load_step(task_id, "detect")["detection"]["detections"]
            assert len(detection) == 1
            assert detection[0]["label"] == "AJ"
            assert detection[0]["native_label"] == "AJ"
            assert detection[0]["score"] == 0.6
            assert tuple(detection[0]["bbox_xyxy"]) == CANDIDATE["bbox_xyxy"]
            assert len(provider_attempts) == len(scenario.verify_statuses)
            assert [call.attempt for call in verify_calls] == list(
                range(1, len(scenario.verify_statuses) + 1)
            )
            assert [str(call.status) for call in verify_calls] == list(
                scenario.verify_statuses
            )
            failed = [call for call in verify_calls if call.error is not None]
            assert failed
            assert {call.error.code for call in failed} == {scenario.error_code}
            finding = result["findings"][0]
            assert finding["canonical_label"] == "AJ"
            assert finding["score"] == 0.6
            assert tuple(finding["evidence"][0]["region"]["bbox_xyxy"]) == CANDIDATE[
                "bbox_xyxy"
            ]
            assert result["inspection_result"] != "normal"

            if scenario.waits_for_review:
                assert repo.load_step(task_id, "verify") is None
                assert result["runtime_status"] == "waiting_review"
                assert result["inspection_result"] == "inconclusive"
                assert finding["review_status"] == "needs_review"
                assert scenario.error_code in result["waiting_reason"]
            else:
                assert repo.load_step(task_id, "verify") is not None
                assert result["runtime_status"] == "completed"
                assert result["inspection_result"] == "defects_found"
                assert finding["review_status"] == "confirmed"
                assert repo.get_task(task_id).snapshot.retry_count == 1

        _write_evidence(
            fault_output_dir,
            scenario,
            repo,
            task_id,
            workflow_id,
            result,
            provider_attempts,
            detector.calls,
        )
    finally:
        if handle and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("test cleanup")
