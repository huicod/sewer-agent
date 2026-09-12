from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.app import create_app
from pipe_inspector.config import Settings
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.enhancement import EnhancementResult, EnhancementReview
from pipe_inspector.contracts.inspection import QualitySummary
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot


def task_fixture():
    now = datetime.now(timezone.utc)
    task_id = uuid4()
    return TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="增强后辅助复核",
        created_at=now,
        snapshot=TaskSnapshot(
            task_id=task_id,
            updated_at=now,
            runtime_status="running",
            stage="tools",
            active_tool="enhancement_review",
        ),
    )


def enhancement_fixture(task):
    quality = QualitySummary(
        mean_luminance=20,
        contrast=8,
        sharpness=4,
        adequate=False,
        reasons=("blur",),
    )
    result = EnhancementResult(
        input_asset=task.input_asset,
        output_asset=AssetRecord(
            ref=AssetRef(asset_id=uuid4()),
            kind="enhanced_image",
            media_type="image/png",
            byte_size=1024,
            width=640,
            height=480,
            parent=task.input_asset,
        ),
        kind="deblur",
        model_id="nafnet",
        model_version="v1",
        source_region=ImageRegion(width=640, height=480, bbox_xyxy=(0, 0, 640, 480)),
        quality_before=quality,
        quality_after=quality,
    )
    review = EnhancementReview(quality_effect="unchanged", summary="未观察到稳定变化")
    return result, review


def app_fixture(tmp_path, *, task=None, result=None, review=None):
    task = task or task_fixture()
    reads = []

    def get_task(task_id):
        reads.append("task")
        assert task_id == task.snapshot.task_id
        return task

    def load_step(task_id, step):
        reads.append(step)
        assert task_id == task.snapshot.task_id
        value = {"enhance": result, "enhancement_review": review}[step]
        return None if value is None else value.model_dump(mode="json")

    repository = SimpleNamespace(get_task=get_task, load_step=load_step)
    app = create_app(Settings(_env_file=None, asset_root=tmp_path), repository=repository)
    return app, task, reads


async def test_enhancement_detail_returns_null_before_any_checkpoint(tmp_path):
    app, task, reads = app_fixture(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/enhancement")
    assert response.status_code == 200
    assert response.json() == {
        "task_id": str(task.snapshot.task_id),
        "result": None,
        "review": None,
    }
    assert reads == ["task", "enhance", "enhancement_review"]


async def test_enhancement_detail_returns_saved_result_during_review_stage(tmp_path):
    task = task_fixture()
    result, _ = enhancement_fixture(task)
    app, stored_task, _ = app_fixture(tmp_path, task=task, result=result)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{stored_task.snapshot.task_id}/enhancement")
    assert response.status_code == 200
    assert response.json()["result"]["kind"] == "deblur"
    assert response.json()["review"] is None


async def test_enhancement_detail_returns_saved_result_and_review(tmp_path):
    task = task_fixture()
    result, review = enhancement_fixture(task)
    app, stored_task, _ = app_fixture(tmp_path, task=task, result=result, review=review)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{stored_task.snapshot.task_id}/enhancement")
    assert response.status_code == 200
    assert response.json()["review"]["quality_effect"] == "unchanged"


async def test_enhancement_detail_rejects_result_owned_by_another_task(tmp_path):
    other_task = task_fixture()
    result, _ = enhancement_fixture(other_task)
    app, task, _ = app_fixture(tmp_path, result=result)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/enhancement")
    assert response.status_code == 500
