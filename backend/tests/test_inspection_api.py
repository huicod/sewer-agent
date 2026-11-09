from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import httpx
import pytest
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetKind, AssetRef
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.video import VideoAggregationState, VideoScanResult
from pipe_inspector.workflow.start import InspectionStartError


@pytest.mark.asyncio
async def test_start_failure_is_safe_and_structured():
    starter = AsyncMock()
    starter.start.side_effect = InspectionStartError("workflow_unavailable", "请重试")
    app = create_app(Settings(_env_file=None), starter=starter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/api/tasks/{uuid4()}/start")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "workflow_unavailable", "message": "请重试"}}
    starter.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_validates_task_id_without_contacting_temporal():
    starter = AsyncMock()
    app = create_app(Settings(_env_file=None), starter=starter)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/tasks/not-a-uuid/start")
    assert response.status_code == 422
    starter.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_returns_task_and_video_detail_reads_only_saved_steps():
    task_id = uuid4()
    task = {
        "request_id": str(uuid4()),
        "session_id": str(uuid4()),
        "input_asset": {"asset_id": str(uuid4()), "version": 1},
        "instruction": "scan video",
        "created_at": "2026-09-10T00:00:00Z",
        "snapshot": {"task_id": str(task_id), "updated_at": "2026-09-10T00:00:00Z"},
        "parent_task_id": None,
    }
    record = TaskRecord.model_validate(task)
    state = _video_state(record.input_asset, finalized=True)
    scan = VideoScanResult(state=state, decode_complete=True).model_dump(mode="json")
    evidence = {
        "frames": [
            {
                "frame_index": 4,
                "timestamp_seconds": 1.25,
                "asset": {"asset_id": str(uuid4()), "version": 1},
            }
        ],
        "review_frames": [4],
    }
    repo = SimpleNamespace(
        get_task=lambda _: record,
        get_asset=lambda _: SimpleNamespace(kind=AssetKind.VIDEO),
        load_video_state=lambda _: state,
        get_video_frame=lambda *_: SimpleNamespace(timestamp_seconds=0.0),
        load_step=lambda _, step: scan if step == "video_scan" else evidence,
    )
    starter = SimpleNamespace(cancel=AsyncMock(return_value=record))
    app = create_app(Settings(_env_file=None), repository=repo, starter=starter)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cancelled = await client.post(f"/api/tasks/{task_id}/cancel")
        detail = await client.get(f"/api/tasks/{task_id}/video")

    assert cancelled.status_code == 202
    assert cancelled.json() == record.model_dump(mode="json")
    starter.cancel.assert_awaited_once_with(task_id)
    assert detail.status_code == 200
    assert detail.json() == {
        "task_id": str(task_id),
        "scan": scan,
        "evidence": evidence,
        "scan_progress": None,
        "scan_start_seconds": 0.0,
    }


@pytest.mark.asyncio
async def test_video_detail_rejects_image_without_loading_video_steps():
    task_id = uuid4()
    repo = SimpleNamespace(
        get_task=lambda _: SimpleNamespace(input_asset=object()),
        get_asset=lambda _: SimpleNamespace(kind=AssetKind.IMAGE),
        load_video_state=lambda *_: pytest.fail("image detail must not read video state"),
        get_video_frame=lambda *_: pytest.fail("image detail must not read video frames"),
        load_step=lambda *_: pytest.fail("image detail must not read video checkpoints"),
    )
    app = create_app(Settings(_env_file=None), repository=repo, starter=AsyncMock())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/tasks/{task_id}/video")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "video_only"


def _video_state(source, *, finalized=False):
    return VideoAggregationState(
        source=source,
        detector_id="primary",
        model_version="test-model",
        label_space_version="five-labels",
        next_frame=8,
        last_timestamp_seconds=7.8,
        adequate_frames=6,
        finalized=finalized,
    )


async def _video_detail(repo, task_id):
    app = create_app(Settings(_env_file=None), repository=repo, starter=AsyncMock())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/tasks/{task_id}/video")


def _video_repository(source, *, state=None, scan=None, evidence=None, first_pts=7.1):
    repo = Mock()
    repo.get_task.return_value = SimpleNamespace(input_asset=source)
    repo.get_asset.return_value = SimpleNamespace(kind=AssetKind.VIDEO)
    repo.load_video_state.return_value = state
    repo.load_step.side_effect = lambda _, step: scan if step == "video_scan" else evidence
    repo.get_video_frame.return_value = SimpleNamespace(timestamp_seconds=first_pts)
    return repo


@pytest.mark.asyncio
@pytest.mark.parametrize("initialized", [False, True])
async def test_video_detail_without_processed_frames_has_no_start_timestamp(initialized):
    task_id = uuid4()
    source = AssetRef(asset_id=uuid4())
    state = VideoAggregationState(source=source) if initialized else None
    repo = _video_repository(source, state=state)

    response = await _video_detail(repo, task_id)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(task_id),
        "scan": None,
        "evidence": None,
        "scan_progress": state.model_dump(mode="json") if state else None,
        "scan_start_seconds": None,
    }
    repo.get_video_frame.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("finalized", [False, True])
async def test_video_detail_exposes_real_progress_and_nonzero_first_pts_without_mutation(finalized):
    task_id = uuid4()
    source = AssetRef(asset_id=uuid4())
    state = _video_state(source, finalized=finalized)
    evidence = {"frames": [], "review_frames": []}
    before = deepcopy((state.model_dump(mode="json"), evidence))
    repo = _video_repository(source, state=state, evidence=evidence)

    response = await _video_detail(repo, task_id)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(task_id),
        "scan": None,
        "evidence": evidence,
        "scan_progress": before[0],
        "scan_start_seconds": 7.1,
    }
    assert (state.model_dump(mode="json"), evidence) == before
    assert repo.mock_calls == [
        call.get_task(task_id),
        call.get_asset(source),
        call.load_video_state(task_id),
        call.load_step(task_id, "video_scan"),
        call.get_video_frame(task_id, 0),
        call.load_step(task_id, "video_evidence"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("head_available", [False, True])
async def test_video_detail_final_scan_wins_over_earlier_head_read(head_available):
    task_id = uuid4()
    source = AssetRef(asset_id=uuid4())
    head = _video_state(source) if head_available else None
    final_state = VideoAggregationState(
        **{**_video_state(source).model_dump(), "next_frame": 12, "finalized": True}
    )
    scan = VideoScanResult(
        state=final_state, decode_complete=False, error_code="decode_failed"
    ).model_dump(mode="json")
    before = deepcopy(scan)
    repo = _video_repository(source, state=head, scan=scan)

    response = await _video_detail(repo, task_id)

    assert response.status_code == 200
    assert response.json()["scan"] == scan == before
    assert response.json()["scan_progress"] is None
    assert response.json()["scan_start_seconds"] == 7.1
    repo.get_video_frame.assert_called_once_with(task_id, 0)
    assert repo.mock_calls.index(call.load_video_state(task_id)) < repo.mock_calls.index(
        call.load_step(task_id, "video_scan")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_source", ["head", "final", "head_with_final"])
async def test_video_detail_rejects_mismatched_source(wrong_source):
    task_id = uuid4()
    source = AssetRef(asset_id=uuid4())
    other_source = AssetRef(asset_id=source.asset_id, version=2)
    head = _video_state(other_source if wrong_source != "final" else source)
    scan = None
    if wrong_source != "head":
        scan = VideoScanResult(
            state=_video_state(other_source if wrong_source == "final" else source),
            decode_complete=True,
        ).model_dump(mode="json")
    repo = _video_repository(source, state=head, scan=scan)

    response = await _video_detail(repo, task_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "video_source_mismatch"
    repo.get_video_frame.assert_not_called()
