"""Read-only selection counterfactual over an existing complete 147.9s scan."""

import json
from pathlib import Path
from uuid import UUID

from pipe_inspector.agent.image_review import review_required
from pipe_inspector.agent.video_policy import select_review_frames
from pipe_inspector.config import Settings
from pipe_inspector.contracts.video import VideoScanResult
from pipe_inspector.storage.video import VideoRepository
from pipe_inspector.workflow.video_selection import select_video_review

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "var/evaluations/2026-09-10/video-long"
OUTPUT = ROOT / "var/evaluations/2026-09-14-video-selection/selection-comparison.json"


def main():
    if OUTPUT.exists():
        raise RuntimeError("Existing comparison must not be overwritten")
    summary = json.loads((SOURCE / "summary.json").read_text())
    scan = VideoScanResult.model_validate_json((SOURCE / "scan.json").read_text())
    repository = VideoRepository(Settings().database_dsn, schema=summary["schema"])
    audit_indices = {frame.frame_index for frame in scan.state.audit_frames}
    indices = audit_indices | {
        frame.frame_index for event in scan.state.events for frame in event.evidence
    }
    observations = {
        index: repository.get_video_frame(UUID(summary["task_id"]), index) for index in indices
    }
    required = [
        {
            frame.frame_index
            for frame in event.evidence
            if review_required(observations[frame.frame_index])
        }
        for event in scan.state.events
    ]
    required = [group for group in required if group]

    def describe(frames):
        return {
            "frames": list(frames),
            "timestamps_seconds": [observations[index].timestamp_seconds for index in frames],
            "selected_audits": len(set(frames) & audit_indices),
            "complete_required_event_groups": sum(group.issubset(frames) for group in required),
        }

    payload = {
        "source_scan": str((SOURCE / "scan.json").relative_to(ROOT)),
        "scope": (
            "Selection-only counterfactual on frozen original detections; "
            "no new YOLO/VLM calls or verdict changes"
        ),
        "processed_frames": scan.state.next_frame,
        "events": len(scan.state.events),
        "required_event_groups": len(required),
        "periodic_audits": len(audit_indices),
        "budgets": {},
    }
    for budget in (4, 8, 32):
        legacy = select_review_frames(scan, observations, budget=budget)
        selection = select_video_review(scan, observations, budget=budget)
        payload["budgets"][str(budget)] = {
            "legacy": describe(legacy),
            "balanced_no_scene": describe(selection.review_frames),
            "selection": selection.model_dump(mode="json"),
        }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({"artifact": str(OUTPUT), "comparison": payload["budgets"]}, ensure_ascii=False)
    )


if __name__ == "__main__":
    main()
