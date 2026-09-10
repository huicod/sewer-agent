import pytest
from pipe_inspector.contracts.assets import AssetRef
from pipe_inspector.contracts.video import FrameObservation, VideoAggregationState
from pipe_inspector.vision.video_events import aggregate_chunk
from pydantic import ValidationError


def frame(index, *, time=None, labels=("PL",), x=1, score=0.7, model="fixture-v1", adequate=True):
    return FrameObservation.model_validate(
        {
            "frame_index": index,
            "timestamp_seconds": index * 0.1 if time is None else time,
            "detection": {
                "detector_id": "fixture",
                "model_version": model,
                "label_space_version": "five",
                "width": 100,
                "height": 80,
                "detections": [
                    {
                        "label": label,
                        "native_label": label,
                        "score": score,
                        "bbox_xyxy": [x + i * 30, 1, x + i * 30 + 20, 25],
                    }
                    for i, label in enumerate(labels)
                ],
            },
            "quality": {
                "mean_luminance": 100,
                "contrast": 40,
                "sharpness": 100,
                "adequate": adequate,
            },
        }
    )


def initial(**kwargs):
    return VideoAggregationState(
        source=AssetRef(asset_id="11111111-1111-4111-8111-111111111111"), **kwargs
    )


def test_cross_chunk_json_replay_preserves_single_event_and_real_times():
    first = aggregate_chunk(initial(), (frame(0, time=7.1), frame(1, time=7.23)))
    reopened = VideoAggregationState.model_validate_json(first.model_dump_json())
    final = aggregate_chunk(
        reopened, (frame(0, time=7.1), frame(1, time=7.23), frame(2, time=7.9)), final=True
    )
    assert final.next_frame == 3
    assert len(final.events) == 1 and not final.active
    event = final.events[0]
    assert event.event_id == first.active[0].event_id
    assert (event.start_frame, event.end_frame, event.start_seconds, event.end_seconds) == (
        0,
        2,
        7.1,
        7.9,
    )
    assert event.observations == 3
    assert aggregate_chunk(final, (frame(2, time=7.9),), final=True) == final


def test_short_single_frame_anomaly_is_not_discarded():
    final = aggregate_chunk(
        initial(), (frame(0, labels=()), frame(1), frame(2, labels=())), final=True
    )
    assert len(final.events) == 1
    assert final.events[0].start_frame == final.events[0].end_frame == 1
    assert final.events[0].observations == 1
    assert final.next_frame == 3


def test_class_and_spatially_separate_boxes_do_not_merge():
    final = aggregate_chunk(
        initial(), (frame(0, labels=("PL", "FS")), frame(1, labels=("PL", "FS"))), final=True
    )
    assert [(e.label, e.observations) for e in final.events] == [("PL", 2), ("FS", 2)]
    separate = aggregate_chunk(initial(), (frame(0), frame(1, x=60)), final=True)
    assert len(separate.events) == 2


def test_one_detection_cannot_update_two_tracks_and_scaled_frames_keep_original_coordinates():
    before = frame(0, labels=("PL", "PL"))
    final = aggregate_chunk(initial(), (before, frame(1)), final=True)
    assert [event.observations for event in final.events] == [2, 1]
    larger = FrameObservation.model_validate(
        frame(1).model_dump()
        | {
            "detection": frame(1).detection.model_dump()
            | {
                "width": 200,
                "height": 160,
                "detections": [
                    {"label": "PL", "native_label": "PL", "score": 0.8, "bbox_xyxy": [2, 2, 42, 50]}
                ],
            },
        }
    )
    scaled = aggregate_chunk(initial(), (frame(0), larger), final=True)
    assert len(scaled.events) == 1
    assert scaled.events[0].last_region.bbox_xyxy == (2, 2, 42, 50)
    assert scaled.events[0].last_region.width == 200


def test_large_time_gap_splits_even_adjacent_frame_indices():
    final = aggregate_chunk(initial(), (frame(0, time=0), frame(1, time=3)), final=True)
    assert len(final.events) == 2


def test_dropped_detection_gap_is_bridged_but_long_gap_is_closed():
    close = aggregate_chunk(initial(), (frame(0), frame(1, labels=()), frame(2)), final=True)
    assert len(close.events) == 1 and close.events[0].observations == 2
    far = aggregate_chunk(
        initial(), (frame(0), *(frame(i, labels=()) for i in range(1, 6)), frame(6)), final=True
    )
    assert len(far.events) == 2


def test_evidence_bounded_and_quality_preferred_with_temporal_spread():
    final = aggregate_chunk(
        initial(),
        tuple(frame(i, time=i * 0.3, score=0.5 + i * 0.01, adequate=i != 4) for i in range(5)),
        final=True,
    )
    evidence = final.events[0].evidence
    assert len(evidence) == 3
    assert {item.frame_index for item in evidence} == {1, 2, 3}
    assert all(item.quality.adequate for item in evidence)


def test_zero_detection_keeps_periodic_negative_audit_candidates_not_normal():
    final = aggregate_chunk(
        initial(),
        (frame(0, time=0, labels=()), frame(1, time=5, labels=()), frame(2, time=10, labels=())),
        final=True,
    )
    assert not final.events
    assert [item.frame_index for item in final.audit_frames] == [0, 2]
    assert final.next_frame == 3 and final.adequate_frames == 3


@pytest.mark.parametrize("bad", ["index_gap", "time_backwards", "model_change", "after_final"])
def test_wrong_sequence_or_model_cannot_merge_into_existing_state(bad):
    state = aggregate_chunk(initial(), (frame(0, time=1),), final=bad == "after_final")
    next_frame = frame(
        2 if bad == "index_gap" else 1,
        time=0.9 if bad == "time_backwards" else 1.1,
        model="new" if bad == "model_change" else "fixture-v1",
    )
    with pytest.raises(ValueError):
        aggregate_chunk(state, (next_frame,))


def test_invalid_timestamps_rejected_at_contract_boundary():
    with pytest.raises(ValidationError):
        frame(0, time=float("nan"))
    with pytest.raises(ValidationError):
        frame(0, time=-1)
