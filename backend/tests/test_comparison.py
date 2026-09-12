"""Pure comparison snapshot and contract tests."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pipe_inspector.comparison import ComparisonNotAllowed, build_comparison
from pipe_inspector.contracts.assets import AssetKind, AssetRecord, AssetRef
from pipe_inspector.contracts.comparison import ComparisonResult
from pipe_inspector.contracts.findings import EvidenceRef, Finding
from pipe_inspector.contracts.intake import TaskRecord
from pipe_inspector.contracts.tasks import TaskSnapshot
from pydantic import ValidationError


def original(kind: AssetKind = AssetKind.IMAGE) -> AssetRecord:
    return AssetRecord(
        ref=AssetRef(asset_id=uuid4()),
        kind=kind,
        media_type="video/mp4" if kind is AssetKind.VIDEO else "image/png",
        byte_size=128,
        width=16,
        height=12,
        duration_seconds=1.0 if kind is AssetKind.VIDEO else None,
    )


def finding(task_id, asset, label, status) -> Finding:
    return Finding(
        finding_id=uuid4(),
        task_id=task_id,
        canonical_label=label,
        open_label="unresolved anomaly" if label is None else None,
        review_status=status,
        source="human" if label == "OTHER" and status == "confirmed" else "detector",
        evidence=(EvidenceRef(asset=asset.ref),),
        confirmed_by=(
            "human" if label == "OTHER" else "rules"
        )
        if status == "confirmed"
        else None,
    )


def task(session_id, asset, *, findings=(), status=None) -> TaskRecord:
    task_id = uuid4()
    bound_findings = tuple(
        finding(task_id, asset, label, review_status)
        for label, review_status in findings
    )
    runtime_status = status or (
        "partial"
        if any(item_status in {"tentative", "needs_review"} for _, item_status in findings)
        else "completed"
    )
    return TaskRecord(
        request_id=uuid4(),
        session_id=session_id,
        input_asset=asset.ref,
        instruction="inspect",
        created_at=datetime.now(timezone.utc),
        snapshot=TaskSnapshot(
            task_id=task_id,
            runtime_status=runtime_status,
            inspection_result="defects_found" if any(
                status == "confirmed" for _, status in findings
            ) else "inconclusive",
            stage="finished",
            stage_progress=1,
            overall_progress=1,
            updated_at=datetime.now(timezone.utc),
            findings=bound_findings,
        ),
    )


def comparison_case():
    session_id = uuid4()
    reference_media = original()
    current_media = original()
    reference = task(
        session_id,
        reference_media,
        findings=(("AJ", "confirmed"), ("FS", "tentative")),
    )
    current = task(
        session_id,
        current_media,
        findings=(
            ("AJ", "confirmed"),
            ("AJ", "confirmed"),
            ("AJ", "rejected"),
            ("OTHER", "confirmed"),
            (None, "needs_review"),
        ),
    )
    return reference, current, reference_media, current_media


def test_build_comparison_counts_each_status_and_only_deltas_confirmed():
    reference, current, reference_media, current_media = comparison_case()
    created_at = datetime(2026, 9, 13, tzinfo=timezone.utc)

    result = build_comparison(
        uuid4(),
        reference,
        current,
        reference_media,
        current_media,
        attempt=2,
        created_at=created_at,
    )

    assert [row.label for row in result.rows] == [
        "AJ",
        "BX",
        "FS",
        "PL",
        "BLOCK",
        "OTHER",
        "UNLABELED",
    ]
    aj = result.rows[0]
    assert aj.reference.model_dump() == {
        "confirmed": 1,
        "tentative": 0,
        "needs_review": 0,
        "rejected": 0,
    }
    assert aj.current.model_dump() == {
        "confirmed": 2,
        "tentative": 0,
        "needs_review": 0,
        "rejected": 1,
    }
    assert aj.confirmed_delta == 1
    assert result.rows[2].reference.tentative == 1
    assert result.rows[5].current.confirmed == 1
    assert result.rows[6].current.needs_review == 1
    assert result.created_at == created_at
    assert result.attempt == 2
    assert result.reference == reference
    assert result.current == current
    assert result.matching_status == "unverified"
    assert result.scope == "saved_findings"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"rows.0.current.confirmed": 3}, "rows"),
        ({"rows.reverse": True}, "rows"),
        ({"current_media.ref": AssetRef(asset_id=uuid4())}, "input asset"),
        ({"current.session_id": uuid4()}, "session"),
    ],
)
def test_comparison_result_rejects_tampered_snapshot(mutation, message):
    reference, current, reference_media, current_media = comparison_case()
    result = build_comparison(
        uuid4(), reference, current, reference_media, current_media
    ).model_dump(mode="json")
    if "rows.0.current.confirmed" in mutation:
        result["rows"][0]["current"]["confirmed"] = mutation[
            "rows.0.current.confirmed"
        ]
    elif mutation.get("rows.reverse"):
        result["rows"].reverse()
    elif "current_media.ref" in mutation:
        result["current_media"]["ref"] = mutation["current_media.ref"].model_dump(
            mode="json"
        )
    else:
        result["current"]["session_id"] = str(mutation["current.session_id"])

    with pytest.raises(ValidationError, match=message):
        ComparisonResult.model_validate(result)


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("same_task", "comparison_same_task"),
        ("pending", "comparison_source_not_ready"),
        ("media_mismatch", "comparison_media_mismatch"),
        ("derived", "comparison_original_only"),
    ],
)
def test_build_comparison_rejects_unsupported_sources(mutation, code):
    reference, current, reference_media, current_media = comparison_case()
    if mutation == "same_task":
        current = reference
        current_media = reference_media
    elif mutation == "pending":
        current = task(current.session_id, current_media, status="running")
    elif mutation == "media_mismatch":
        current_media = original(AssetKind.VIDEO)
        current = task(reference.session_id, current_media)
    else:
        derived_ref = AssetRef(asset_id=uuid4())
        current_media = AssetRecord(
            ref=derived_ref,
            kind="crop",
            media_type="image/png",
            byte_size=32,
            width=8,
            height=6,
            parent=current.input_asset,
        )
        current = TaskRecord.model_validate(
            current.model_dump(mode="json")
            | {"input_asset": derived_ref.model_dump(mode="json")}
        )

    with pytest.raises(ComparisonNotAllowed) as caught:
        build_comparison(
            uuid4(), reference, current, reference_media, current_media
        )
    assert caught.value.code == code
    assert str(caught.value) == code
