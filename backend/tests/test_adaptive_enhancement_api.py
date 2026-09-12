"""Public adaptive-enhancement detail and report projections."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipe_inspector.api.inspection import load_enhancement_detail
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.enhancement import EnhancementSelection
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.reports import TaskReport
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.reports import render_html
from pydantic import ValidationError


def task_fixture() -> TaskRecord:
    now = datetime.now(timezone.utc)
    return TaskRecord(
        request_id=uuid4(),
        session_id=uuid4(),
        input_asset=AssetRef(asset_id=uuid4()),
        instruction="必要时按需增强后检查",
        created_at=now,
        snapshot=TaskSnapshot(
            task_id=uuid4(),
            updated_at=now,
            runtime_status="partial",
            inspection_result="inconclusive",
        ),
    )


def selected_low_light() -> EnhancementSelection:
    return EnhancementSelection.model_validate(
        {
            "status": "selected",
            "reason": "低照度清晰影响判读，允许执行恢复",
            "proposal": {
                "options": {"kind": "low_light", "roi_xyxy": None},
                "degradation": "low_light",
                "confidence": "clear",
                "evidence": "管壁暗部细节缺失",
                "inspection_impact": "裂纹边缘难以辨认",
            },
            "options": {"kind": "low_light", "roi_xyxy": None},
        }
    )


def test_detail_reads_only_the_public_selection_from_its_checkpoint():
    task = task_fixture()
    selection = selected_low_light()
    reads: list[str] = []

    def load_step(_task_id, step):
        reads.append(step)
        if step == "enhancement_selection":
            return {"selection": selection.model_dump(mode="json")}
        return None

    detail = load_enhancement_detail(SimpleNamespace(load_step=load_step), task)

    assert detail.selection == selection
    assert detail.result is None and detail.review is None
    assert reads == ["enhance", "enhancement_review", "enhancement_selection"]
    assert "model_path" not in detail.model_dump_json()


@pytest.mark.parametrize(
    "checkpoint",
    [
        {
            "selection": selected_low_light().model_dump(mode="json"),
            "config": {"model_path": "/srv/private.pt"},
        },
        {
            "selection": selected_low_light().model_dump(mode="json")
            | {"model_path": "/srv/private.pt"}
        },
    ],
)
def test_detail_rejects_checkpoint_or_selection_server_configuration(checkpoint):
    task = task_fixture()
    repository = SimpleNamespace(
        load_step=lambda _task_id, step: checkpoint if step == "enhancement_selection" else None
    )

    with pytest.raises((ValueError, ValidationError)):
        load_enhancement_detail(repository, task)


def test_report_keeps_optional_selection_and_renders_skip_without_an_image():
    task = task_fixture()
    selection = EnhancementSelection.model_validate(
        {
            "status": "skipped",
            "reason": '<img src=x onerror="alert(1)">建议置信度不足，保持原图证据',
            "proposal": {
                "options": None,
                "degradation": "mixed_dark_noise_blur",
                "confidence": "uncertain",
                "evidence": "暗部与噪声同时存在",
                "inspection_impact": "无法确定单一工具能改善判读",
            },
            "options": None,
        }
    )

    report = TaskReport(task=task, enhancement_selection=selection)
    payload = report.model_dump(mode="json")
    html = render_html(report)

    assert payload["enhancement_selection"] == selection.model_dump(mode="json")
    assert "自动增强已跳过" in html and "建议置信度不足" in html
    assert "暗部与噪声同时存在" in html
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=&quot;" in html


def test_old_report_payload_defaults_selection_to_null():
    task = task_fixture()
    report = TaskReport.model_validate(
        {"schema_version": "1", "task": task.model_dump(mode="json")}
    )

    assert report.enhancement_selection is None
