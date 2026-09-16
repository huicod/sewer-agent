"""Compare current online logic with saved accepted evidence, without model calls."""

import json
from pathlib import Path
from uuid import uuid4

from pipe_inspector.agent.guided_fusion import fuse_guided
from pipe_inspector.agent.guided_review import GuidedReview
from pipe_inspector.agent.review_pipeline import decide_for_run
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.inspection import ImageObservation


def main():
    root = Path(__file__).resolve().parents[3]
    source = root / "var/evaluations/2026-09-11-confidence"
    results = []
    for group in ("paired", "regression"):
        saved = json.loads((source / group / "state.json").read_text())
        for record in saved["records"]:
            observation = ImageObservation.model_validate(record["observation"])
            review = GuidedReview.model_validate(record["review"]) if record["review"] else None
            actual = fuse_guided(observation, review)
            assert actual["predictions"] == record["predictions"], (group, record["sample_id"])
            assert actual["changes"] == record["changes"], (group, record["sample_id"])
            decision = decide_for_run(
                {"image_review_mode": "guided_review"}, uuid4(), AssetRef(asset_id=uuid4()),
                observation, review, review_error=record["review_error"],
            )
            selected = []
            for change, finding in zip(actual["changes"], decision.findings, strict=True):
                if change["action"] == "reject":
                    assert finding.review_status == "rejected"
                    continue
                if change["action"] == "add_pending":
                    assert finding.review_status == "needs_review"
                    continue
                selected.append({
                    "label": finding.canonical_label,
                    "bbox_xyxy": list(finding.evidence[0].region.bbox_xyxy),
                    # Offline 1.0 is a scoring sentinel, not VLM confidence.
                    "score": 1.0 if finding.source == "vlm" else finding.score,
                })
                if finding.source == "vlm":
                    assert finding.score is None
            assert selected == record["predictions"], (group, record["sample_id"])
            results.append({"group": group, "sample_id": record["sample_id"],
                            "predictions": len(selected), "parity": True,
                            "saved_review_error": record["review_error"]})
    output = root / "var/evaluations/2026-09-16-online-guided-parity"
    output.mkdir(parents=True, exist_ok=True)
    summary = {"images": len(results), "matched": len(results), "model_calls": 0,
               "source": str(source), "results": results}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
