import base64
import json
from io import BytesIO

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary


def observation(count=1, score=0.9):
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="cached",
            model_version="fixed",
            label_space_version="five",
            width=640,
            height=480,
            detections=tuple(
                Detection(
                    label="PL", native_label="PL", score=score, bbox_xyxy=(200, 100, 300, 200)
                )
                for _ in range(count)
            ),
        ),
        quality=QualitySummary(mean_luminance=100, contrast=30, sharpness=80, adequate=True),
    )


def candidate(index=0, verdict="accept", strength="clear", label=None):
    return dict(
        index=index,
        verdict=verdict,
        evidence_strength=strength,
        proposed_label=label,
        reason="候选区域可见证据",
    )


def response(candidates=None, additions=(), assessable=True):
    return dict(
        coordinate_space="normalized_0_1000",
        image_assessable=assessable,
        full_image_audited=True,
        candidates=candidates if candidates is not None else [candidate()],
        additional_anomalies=additions,
        summary="逐框及整图检查",
    )


class Client:
    def __init__(self, payload):
        self.payload, self.requests = payload, []

    async def generate(self, messages, response_model):
        self.requests.append(messages)
        return response_model.model_validate(self.payload)


@pytest.mark.asyncio
async def test_guided_input_has_exact_original_indexed_native_crops_and_no_gt(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.png"
    source = Image.new("RGB", (640, 480), (5, 17, 29))
    source.putpixel((200, 100), (80, 90, 100))
    source.save(path)
    original = path.read_bytes()
    client = Client(response())
    await GuidedImageReviewer(client).review(path, observation())
    content = client.requests[0][1]["content"]
    metadata = json.loads(content[0]["text"])
    assert len(client.requests) == 1
    assert metadata["candidates"][0]["index"] == 0
    assert metadata["candidates"][0]["bbox_xyxy"] == [200, 100, 300, 200]
    assert metadata["candidates"][0]["bbox_normalized_0_1000"] == pytest.approx(
        [312.5, 208.333333, 468.75, 416.666667]
    )
    assert set(metadata["label_guidance"]) == {"AJ", "BX", "FS", "PL", "BLOCK"}
    assert "ground_truth" not in json.dumps(metadata)
    assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == original
    view = metadata["views"][1]
    assert view["candidate_index"] == 0
    crop = Image.open(BytesIO(base64.b64decode(content[2]["image_url"]["url"].split(",")[1])))
    assert crop.getpixel((200 - view["origin_xy"][0], 100 - view["origin_xy"][1])) == (80, 90, 100)
    assert crop.size == (view["width"], view["height"])
    assert path.read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 12])
async def test_whole_image_always_present_and_crop_count_bounded(tmp_path, count):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (640, 480)).save(path)
    client = Client(response([candidate(index=i) for i in range(count)]))
    await GuidedImageReviewer(client).review(path, observation(count))
    metadata = json.loads(client.requests[0][1]["content"][0]["text"])
    assert len(metadata["views"]) == 1 + min(count, 8)
    assert len(metadata["candidates"]) == count


@pytest.mark.asyncio
async def test_additional_box_maps_from_whole_image_normalized_coordinates(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (640, 480)).save(path)
    item = dict(
        label="BLOCK", bbox_xyxy=[500, 500, 750, 750], reason="可见结垢", evidence_strength="clear"
    )
    review = await GuidedImageReviewer(Client(response(additions=[item]))).review(
        path, observation()
    )
    assert review.additional_anomalies[0].bbox_xyxy == (320, 240, 480, 360)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        response([]),
        response([candidate(), candidate()]),
        response([candidate(verdict="revise", label="PL")]),
        response([candidate(label="FS")]),
        response(
            additions=[
                dict(
                    label="PL", bbox_xyxy=[0, 0, 1001, 50], reason="裂纹", evidence_strength="clear"
                )
            ]
        ),
    ],
)
async def test_invalid_indices_contradictory_labels_or_coordinates_fail_closed(tmp_path, payload):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (640, 480)).save(path)
    with pytest.raises(APIClientError) as caught:
        await GuidedImageReviewer(Client(payload)).review(path, observation())
    assert caught.value.code == "response_schema_invalid"


def test_fusion_revises_in_place_and_rejects_without_mutating_observation():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    obs = observation(2, score=0.7)
    before = obs.model_dump()
    payload = response([candidate(0, "revise", label="BLOCK"), candidate(1, "reject")])
    payload.pop("coordinate_space")
    result = fuse_guided(obs, GuidedReview.model_validate(payload))
    assert [p["label"] for p in result["predictions"]] == ["BLOCK"]
    assert result["predictions"][0]["bbox_xyxy"] == [200, 100, 300, 200]
    assert [c["action"] for c in result["changes"]] == ["revise", "reject"]
    assert obs.model_dump() == before


@pytest.mark.parametrize("score", [0.85, 0.8661702871, 0.9408751130, 0.9433636069])
@pytest.mark.parametrize("verdict,label", [("reject", None), ("revise", "BLOCK")])
def test_high_confidence_disagreement_keeps_exact_detector_evidence(score, verdict, label):
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    obs = observation(score=score)
    before = obs.model_dump()
    payload = response([candidate(verdict=verdict, label=label)])
    payload.pop("coordinate_space")
    result = fuse_guided(obs, GuidedReview.model_validate(payload))
    assert result["predictions"] == [dict(label="PL", bbox_xyxy=[200, 100, 300, 200], score=score)]
    change = result["changes"][0]
    assert change["action"] == "retain_high_confidence"
    assert change["pending"] is True
    assert change["vlm_verdict"] == verdict
    assert change["proposed_label"] == label
    assert "高置信度" in change["gate"]
    assert obs.model_dump() == before


@pytest.mark.parametrize("score", [0.8499, 0.7123959661, 0.5573102832, 0.3012649715])
def test_low_confidence_clear_rejection_remains_available(score):
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    payload = response([candidate(verdict="reject")])
    payload.pop("coordinate_space")
    result = fuse_guided(observation(score=score), GuidedReview.model_validate(payload))
    assert result["predictions"] == []
    assert result["changes"][0]["action"] == "reject"
    assert result["changes"][0]["pending"] is False


@pytest.mark.asyncio
async def test_prompt_and_candidate_metadata_expose_confidence_policy(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (640, 480)).save(path)
    obs = observation(2).model_dump()
    obs["detection"]["detections"][0]["score"] = 0.85
    obs["detection"]["detections"][1]["score"] = 0.8499
    client = Client(response([candidate(0), candidate(1)]))
    await GuidedImageReviewer(client).review(path, ImageObservation.model_validate(obs))
    messages = client.requests[0]
    metadata = json.loads(messages[1]["content"][0]["text"])
    assert metadata["confidence_policy"]["high_score_threshold"] == 0.85
    assert [c["protected_by_detector_prior"] for c in metadata["candidates"]] == [True, False]
    assert "高置信度" in messages[0]["content"]
    assert "低置信度" in messages[0]["content"]
    assert len(metadata["views"]) == 3


@pytest.mark.parametrize("review_type", ["failure", "uncertain", "unassessable"])
def test_uncertainty_and_failure_keep_raw_in_scoring_denominator(review_type):
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    payload = response(
        [
            candidate(
                verdict="reject", strength="uncertain" if review_type == "uncertain" else "clear"
            )
        ],
        assessable=review_type != "unassessable",
    )
    payload.pop("coordinate_space")
    review = None if review_type == "failure" else GuidedReview.model_validate(payload)
    result = fuse_guided(observation(), review)
    assert len(result["predictions"]) == 1
    assert result["predictions"][0]["label"] == "PL"
    assert result["changes"][0]["pending"] is True


def test_only_clear_localized_nonoverlapping_five_class_additions_become_predictions():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    def extra(label="FS", box=(400, 300, 450, 350), strength="clear"):
        return dict(label=label, bbox_xyxy=box, evidence_strength=strength, reason="新增可见特征")

    payload = response(
        additions=[
            extra(),
            extra(),
            extra("BLOCK", (200, 100, 300, 200)),
            extra("OTHER", (0, 0, 10, 10)),
            extra(box=None),
            extra(box=(20, 20, 50, 50), strength="uncertain"),
        ]
    )
    payload.pop("coordinate_space")
    result = fuse_guided(observation(), GuidedReview.model_validate(payload))
    assert [p["label"] for p in result["predictions"]] == ["PL", "FS"]
    assert len(result["changes"]) == 7


def test_zero_detector_clear_addition_can_recover_target():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    payload = response(
        [],
        additions=[
            dict(
                label="PL", bbox_xyxy=[30, 30, 50, 50], reason="材料断裂", evidence_strength="clear"
            )
        ],
    )
    payload.pop("coordinate_space")
    assert (
        len(fuse_guided(observation(0), GuidedReview.model_validate(payload))["predictions"]) == 1
    )


@pytest.mark.asyncio
async def test_crop_budget_can_skip_views_without_losing_candidates(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "image.jpg"
    Image.effect_noise((640, 480), 100).convert("RGB").save(path, quality=5)
    original = path.read_bytes()
    client = Client(response())
    obs = observation().model_dump()
    obs["detection"]["detections"][0]["bbox_xyxy"] = (0, 0, 640, 480)
    await GuidedImageReviewer(client, max_image_bytes=len(original)).review(
        path, ImageObservation.model_validate(obs)
    )
    content = client.requests[0][1]["content"]
    metadata = json.loads(content[0]["text"])
    assert len(metadata["views"]) == 1
    assert len(metadata["candidates"]) == 1
    assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == original


@pytest.mark.asyncio
async def test_edge_candidate_crop_stays_in_image(tmp_path):
    from pipe_inspector.agent.guided_review import GuidedImageReviewer

    path = tmp_path / "edge.png"
    Image.new("RGB", (640, 480)).save(path)
    obs = observation().model_dump()
    obs["detection"]["detections"][0]["bbox_xyxy"] = (0, 0, 10, 10)
    client = Client(response())
    await GuidedImageReviewer(client).review(path, ImageObservation.model_validate(obs))
    view = json.loads(client.requests[0][1]["content"][0]["text"])["views"][1]
    assert view["origin_xy"] == [0, 0]
    assert (view["width"], view["height"]) == (69, 69)


def test_uncertain_accept_does_not_become_accepted_or_disappear():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    payload = response([candidate(strength="uncertain")])
    payload.pop("coordinate_space")
    result = fuse_guided(observation(), GuidedReview.model_validate(payload))
    assert result["changes"][0]["action"] == "retain_pending"
    assert result["predictions"][0]["score"] == 0.9


def test_cross_label_additions_at_same_location_are_both_pending_not_two_predictions():
    from pipe_inspector.agent.guided_review import GuidedReview
    from pipe_inspector.evaluation.guided import fuse_guided

    payload = response(
        [],
        additions=[
            dict(
                label=label,
                bbox_xyxy=[30, 30, 50, 50],
                reason="同一区域的相互冲突解释",
                evidence_strength="clear",
            )
            for label in ["PL", "FS"]
        ],
    )
    payload.pop("coordinate_space")
    result = fuse_guided(observation(0), GuidedReview.model_validate(payload))
    assert result["predictions"] == []
    assert [c["action"] for c in result["changes"]] == ["add_pending", "add_pending"]
    assert all("冲突" in c["gate"] for c in result["changes"])
