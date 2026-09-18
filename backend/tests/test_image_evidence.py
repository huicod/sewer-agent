"""Stored evidence must not relabel the detector's confidence or run models."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pipe_inspector.api.inspection import inspection_router
from pipe_inspector.api.reports import report_router
from pipe_inspector.contracts.detection import DetectionLabel
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import TaskRecord

from backend.tests.test_guided_policy import _candidate, _detection, _observation, _review
from backend.tests.test_reports import report_fixture


def evidence_app(*, missing=False, legacy=False, parent=False):
    task, _ = report_fixture()
    observation = _observation(_detection(label=DetectionLabel.FS, score=0.8038))
    review = _review([_candidate(verdict="revise", proposed_label="PL")])
    finding = Finding(
        finding_id=uuid4(),
        task_id=task.snapshot.task_id,
        canonical_label="PL",
        review_status="confirmed",
        source="detector",
        score=0.8038,
        confirmed_by="rules",
        evidence=(EvidenceRef(asset=task.input_asset),),
    )
    task = TaskRecord.model_validate(
        task.model_dump()
        | {
            "parent_task_id": uuid4() if parent else None,
            "snapshot": task.snapshot.model_dump() | {"findings": [finding.model_dump()]},
        }
    )
    raw_review = review.model_dump(mode="json")
    if legacy:
        raw_review["candidates"][0].pop("evidence_strength")
    changes = [
        {
            "index": 0,
            "action": "revise",
            "pending": False,
            "label": "PL",
            "bbox_xyxy": [10, 10, 30, 30],
            "reason": "<script>形态支持裂纹</script>",
            "vlm_verdict": "revise",
            "proposed_label": "PL",
            "gate": None,
        }
    ]
    steps = (
        {}
        if missing
        else {
            "detect": observation.model_dump(mode="json"),
            "verify": {"review": raw_review, "skipped": False},
        }
    )
    if not missing and not legacy:
        steps["verify"]["fusion"] = {
            "changes": changes,
            "predictions": [{"label": "PL", "score": 1.0, "bbox_xyxy": [10, 10, 30, 30]}],
        }
    reads = []

    def load_step(task_id, step):
        assert task_id == task.snapshot.task_id
        reads.append(step)
        return steps.get(step)

    # No write, reviewer, model, or workflow methods exist on this repository.
    repo = SimpleNamespace(get_task=lambda _: task, load_step=load_step, list_calls=lambda _: ())
    app = FastAPI()
    app.include_router(inspection_router(repo, None))
    app.include_router(report_router(repo))
    return app, task, steps, reads


async def test_evidence_keeps_original_score_with_fs_not_vlm_revised_pl():
    app, task, _, _ = evidence_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/image-evidence")
    assert response.status_code == 200
    data = response.json()
    original = data["observation"]["detection"]["detections"][0]
    assert (original["label"], original["score"]) == ("FS", 0.8038)
    assert data["review"]["candidates"][0]["proposed_label"] == "PL"
    assert data["fusion_changes"][0]["action"] == "revise"
    assert data["findings"][0]["canonical_label"] == "PL"
    assert "predictions" not in data and "score" not in data["fusion_changes"][0]


@pytest.mark.parametrize("missing,legacy", [(True, False), (False, True)])
async def test_absent_or_legacy_evidence_is_not_recomputed(missing, legacy):
    app, task, _, reads = evidence_app(missing=missing, legacy=legacy, parent=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/image-evidence")
    assert response.status_code == 200
    data = response.json()
    assert data["parent_task_id"] == str(task.parent_task_id)
    assert data["fusion_changes"] == []
    if missing:
        assert data["observation"] is None and data["review"] is None
    else:
        assert data["review"]["candidates"][0]["evidence_strength"] is None
    assert reads == ["detect", "verify"]


async def test_report_embeds_same_stored_lineage_and_html_separates_score_origin():
    app, task, _, _ = evidence_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        report = await client.get(f"/api/tasks/{task.snapshot.task_id}/report")
        html = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=html")
    assert report.status_code == html.status_code == 200
    original = report.json()["image_evidence"]["observation"]["detection"]["detections"][0]
    assert original["label"] == "FS" and original["score"] == 0.8038
    assert "YOLO / 主检测器原始结果" in html.text
    assert "VLM 复核意见" in html.text and "revise" in html.text
    assert "原检测候选分数" in html.text and "不是融合类别置信度" in html.text
    assert "&lt;script&gt;" in html.text and "<script>" not in html.text


async def test_report_renders_coexisting_label_advice_without_relabeling_original():
    app, task, steps, _ = evidence_app()
    steps["verify"]["review"]["candidates"][0]["coexisting_labels"] = ["PL"]
    steps["verify"]["review"]["candidates"][0]["original_presence"] = "present"
    steps["verify"]["review"]["additional_anomalies"] = [
        {
            "label": "FS",
            "name": None,
            "bbox_xyxy": [1, 2, 3, 4],
            "reason": "独立回看",
            "evidence_strength": "clear",
            "confirmation_status": "confirmed",
        }
    ]
    steps["verify"]["fusion"]["changes"][0]["coexisting_labels"] = ["PL"]
    steps["verify"]["fusion"]["changes"][0]["original_presence"] = "present"
    steps["verify"]["fusion"]["changes"][0]["confirmation_status"] = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        html = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=html")
    assert html.status_code == 200
    assert html.text.count("共存建议 PL") == 2
    assert "原类别判断 原类别存在" in html.text
    assert "新增二次确认 已完成独立回看" in html.text


async def test_empty_detection_and_vlm_addition_preserve_no_numeric_vlm_score():
    app, task, steps, _ = evidence_app()
    steps["detect"]["detection"]["detections"] = []
    steps["verify"] = {
        "review": {
            "image_assessable": True,
            "full_image_audited": True,
            "candidates": [],
            "additional_anomalies": [
                {
                    "label": "PL",
                    "name": None,
                    "bbox_xyxy": [1, 2, 3, 4],
                    "reason": "连续裂缝",
                    "evidence_strength": "clear",
                }
            ],
            "summary": "主检测零候选，VLM 补充裂缝",
        },
        "skipped": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/image-evidence")
    assert response.status_code == 200
    data = response.json()
    assert data["observation"]["detection"]["detections"] == []
    assert data["review"]["additional_anomalies"][0]["label"] == "PL"
    assert "score" not in data["review"]["additional_anomalies"][0]


async def test_report_without_image_checkpoints_does_not_claim_image_lineage():
    # Whole-video runs have video_scan/video_verify, not image detect/verify.
    app, task, steps, _ = evidence_app(missing=True)
    steps["video_scan"] = {"state": {"source": task.input_asset.model_dump(mode="json")}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/tasks/{task.snapshot.task_id}/report")
        html = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=html")
    assert response.status_code == html.status_code == 200
    assert response.json()["image_evidence"] is None
    assert "原类别见检测溯源" not in html.text
    assert "原检测候选分数：0.8038" in html.text


@pytest.mark.parametrize("strength,pending", [("clear", False), ("uncertain", True)])
async def test_html_exposes_saved_addition_strength_and_fusion_pending(strength, pending):
    app, task, steps, _ = evidence_app()
    steps["verify"]["review"]["additional_anomalies"] = [
        {
            "label": "PL",
            "name": None,
            "bbox_xyxy": [40, 40, 50, 50],
            "reason": "局部连续裂纹",
            "evidence_strength": strength,
        }
    ]
    steps["verify"]["fusion"]["changes"] = [
        {
            "additional_index": 0,
            "action": "add_pending" if pending else "add",
            "pending": pending,
            "label": "PL",
            "bbox_xyxy": [40, 40, 50, 50],
            "reason": "局部连续裂纹",
            "gate": "证据门控",
        }
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        html = await client.get(f"/api/tasks/{task.snapshot.task_id}/report?format=html")
    assert html.status_code == 200
    assert f"新增证据强度：{strength}" in html.text
    assert ("融合状态：待人工处理" if pending else "融合状态：已自动处理") in html.text
