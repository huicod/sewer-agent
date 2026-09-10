from datetime import datetime, timezone

import pytest
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.common import ImageRegion
from pipe_inspector.contracts.findings import Finding
from pipe_inspector.contracts.tasks import TaskSnapshot
from pydantic import ValidationError

TASK_ID = "00000000-0000-0000-0000-000000000001"
ASSET_ID = "00000000-0000-0000-0000-000000000002"
FINDING_ID = "00000000-0000-0000-0000-000000000003"
OTHER_ASSET_ID = "00000000-0000-0000-0000-000000000004"


def finding_data(**changes):
    data = {
        "finding_id": FINDING_ID,
        "task_id": TASK_ID,
        "canonical_label": "AJ",
        "source": "detector",
        "evidence": [{"asset": {"asset_id": ASSET_ID, "version": 1}}],
        "score": 0.9,
    }
    return data | changes


def snapshot_data(**changes):
    return {
        "task_id": TASK_ID,
        "updated_at": datetime(2026, 9, 9, tzinfo=timezone.utc),
    } | changes


def test_cancel_capability_is_false_by_default_and_forbidden_on_terminal_tasks():
    assert not TaskSnapshot.model_validate(snapshot_data()).can_cancel
    assert TaskSnapshot.model_validate(snapshot_data(can_cancel=True)).can_cancel
    with pytest.raises(ValidationError, match="terminal tasks cannot be cancelled"):
        TaskSnapshot.model_validate(snapshot_data(runtime_status="cancelled", can_cancel=True))


@pytest.mark.parametrize(
    "data",
    [
        {"asset_id": "../image.jpg"},
        {"asset_id": ASSET_ID, "version": 0},
        {"asset_id": ASSET_ID, "path": "/etc/passwd"},
    ],
)
def test_asset_references_reject_paths_invalid_ids_and_versions(data):
    with pytest.raises(ValidationError):
        AssetRef.model_validate(data)


def test_media_asset_roundtrip_preserves_origin_and_dimensions():
    asset = AssetRecord.model_validate(
        {
            "ref": {"asset_id": ASSET_ID, "version": 1},
            "kind": "crop",
            "media_type": "image/jpeg",
            "byte_size": 128,
            "width": 20,
            "height": 30,
            "parent": {"asset_id": OTHER_ASSET_ID, "version": 2},
        }
    )
    restored = AssetRecord.model_validate_json(asset.model_dump_json())
    assert restored.parent.version == 2
    assert str(restored.parent.asset_id) == OTHER_ASSET_ID
    assert (restored.width, restored.height) == (20, 30)


@pytest.mark.parametrize(
    "changes",
    [
        {"width": None},
        {"byte_size": 0},
        {"duration_seconds": 1.0},
        {"kind": "video"},
        {"kind": "enhanced_image"},
        {"parent": {"asset_id": ASSET_ID, "version": 1}},
    ],
)
def test_media_asset_rejects_inconsistent_metadata(changes):
    data = {
        "ref": {"asset_id": ASSET_ID},
        "kind": "image",
        "media_type": "image/jpeg",
        "byte_size": 128,
        "width": 20,
        "height": 30,
    } | changes
    with pytest.raises(ValidationError):
        AssetRecord.model_validate(data)


@pytest.mark.parametrize(
    "bbox",
    [(1, 1, 0, 2), (-1, 0, 3, 4), (0, 0, 21, 4), (0, 0, float("nan"), 4)],
)
def test_image_region_rejects_invalid_original_pixel_geometry(bbox):
    with pytest.raises(ValidationError):
        ImageRegion(width=20, height=30, bbox_xyxy=bbox)


@pytest.mark.parametrize("label", ["NORMAL", "UNCERTAIN", "unrecognized"])
def test_finding_does_not_turn_conclusions_into_object_classes(label):
    with pytest.raises(ValidationError):
        Finding.model_validate(finding_data(canonical_label=label))


def test_other_finding_can_be_named_but_only_human_confirmed():
    pending = Finding.model_validate(
        finding_data(canonical_label="OTHER", open_label="疑似树根", review_status="needs_review")
    )
    assert pending.open_label == "疑似树根"
    assert pending.review_status == "needs_review"
    with pytest.raises(ValidationError):
        Finding.model_validate(
            finding_data(canonical_label="OTHER", review_status="confirmed", confirmed_by="rules")
        )
    confirmed = Finding.model_validate(
        finding_data(canonical_label="OTHER", review_status="confirmed", confirmed_by="human")
    )
    assert confirmed.canonical_label == "OTHER"


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": []},
        {"review_status": "confirmed"},
        {"review_status": "tentative", "confirmed_by": "human"},
        {"canonical_label": None, "review_status": "confirmed", "confirmed_by": "human"},
        {"score": float("inf")},
        {"evidence": [{"asset": {"asset_id": ASSET_ID}, "frame_index": 2}]},
    ],
)
def test_finding_rejects_missing_evidence_or_inconsistent_confirmation(changes):
    with pytest.raises(ValidationError):
        Finding.model_validate(finding_data(**changes))


def test_waiting_review_survives_json_roundtrip_as_nonterminal():
    snapshot = TaskSnapshot.model_validate(
        snapshot_data(
            runtime_status="waiting_review",
            stage="human_review",
            waiting_reason="其他疑似类别需要确认",
            findings=[finding_data(canonical_label="OTHER", review_status="needs_review")],
            stage_progress=1,
            overall_progress=0.7,
        )
    )
    restored = TaskSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored.is_terminal is False
    assert restored.inspection_result == "pending"
    assert restored.overall_progress == 0.7
    assert restored.findings[0].canonical_label == "OTHER"


@pytest.mark.parametrize(
    "coverage",
    [
        {},
        {"sufficient_coverage": True, "sufficient_quality": True},
        {"sufficient_quality": True, "negative_audit_complete": True},
        {"sufficient_coverage": True, "negative_audit_complete": True},
    ],
)
def test_normal_requires_complete_coverage_quality_and_negative_audit(coverage):
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(snapshot_data(inspection_result="normal", coverage=coverage))


@pytest.mark.parametrize("review_status", ["tentative", "needs_review", "confirmed"])
def test_normal_rejects_unresolved_or_confirmed_findings(review_status):
    finding = finding_data(review_status=review_status)
    if review_status == "confirmed":
        finding["confirmed_by"] = "rules"
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(
            snapshot_data(
                inspection_result="normal",
                coverage={
                    "sufficient_coverage": True,
                    "sufficient_quality": True,
                    "negative_audit_complete": True,
                },
                findings=[finding],
            )
        )


def test_normal_can_follow_rejected_candidates_with_sufficient_evidence():
    snapshot = TaskSnapshot.model_validate(
        snapshot_data(
            runtime_status="completed",
            inspection_result="normal",
            stage="finished",
            stage_progress=1,
            overall_progress=1,
            coverage={
                "sufficient_coverage": True,
                "sufficient_quality": True,
                "negative_audit_complete": True,
            },
            findings=[finding_data(review_status="rejected")],
        )
    )
    assert snapshot.is_terminal
    assert snapshot.model_dump(mode="json")["inspection_result"] == "normal"


def test_confirmed_defect_can_coexist_with_incomplete_coverage():
    snapshot = TaskSnapshot.model_validate(
        snapshot_data(
            runtime_status="partial",
            inspection_result="defects_found",
            findings=[finding_data(review_status="confirmed", confirmed_by="rules")],
        )
    )
    assert not snapshot.coverage.sufficient_coverage
    assert snapshot.findings[0].canonical_label == "AJ"


@pytest.mark.parametrize(
    "changes",
    [
        {"runtime_status": "waiting_review"},
        {"inspection_result": "defects_found"},
        {"runtime_status": "completed"},
        {"overall_progress": 1.1},
        {"processed_frames": -1},
        {"updated_at": "2026-09-09T12:00:00"},
        {"findings": [finding_data(task_id=OTHER_ASSET_ID)]},
    ],
)
def test_task_snapshot_rejects_inconsistent_status_progress_or_identity(changes):
    with pytest.raises(ValidationError):
        TaskSnapshot.model_validate(snapshot_data(**changes))


@pytest.mark.parametrize("extra_field", ["path", "command"])
def test_detector_result_rejects_unexpected_execution_fields(extra_field):
    from pipe_inspector.contracts.detection import Detection

    data = {
        "label": "AJ",
        "native_label": "AJ",
        "score": 0.9,
        "bbox_xyxy": [1, 2, 10, 20],
        extra_field: "/untrusted/input",
    }
    with pytest.raises(ValidationError):
        Detection.model_validate(data)
