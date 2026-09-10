import base64
import json

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import (
    AdditionalAnomaly,
    CandidateReview,
    ImageObservation,
    ImageReview,
    QualitySummary,
)


def observation() -> ImageObservation:
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="sewer-yolo26",
            model_version="best-v1",
            label_space_version="sewer-five-v1",
            width=64,
            height=48,
            detections=(
                Detection(
                    label="AJ",
                    native_label="AJ",
                    score=0.91,
                    bbox_xyxy=(2, 3, 20, 30),
                ),
            ),
        ),
        quality=QualitySummary(
            mean_luminance=100,
            contrast=30,
            sharpness=80,
            adequate=True,
            reasons=(),
        ),
    )


def valid_review(**changes) -> ImageReview:
    values = {
        "image_assessable": True,
        "full_image_audited": True,
        "candidates": (
            CandidateReview(index=0, verdict="accept", reason="复核一致"),
        ),
        "additional_anomalies": (),
        "summary": "完整图像已审核",
    }
    return ImageReview(**(values | changes))


class FakeClient:
    def __init__(self, result: ImageReview) -> None:
        self.result = result
        self.calls = []

    async def generate(self, messages, response_model):
        self.calls.append((messages, response_model))
        return self.result


def save_jpeg(path) -> bytes:
    Image.new("RGB", (64, 48), color=(90, 110, 100)).save(path, format="JPEG")
    return path.read_bytes()


@pytest.mark.asyncio
async def test_reviewer_sends_original_image_and_indexed_metadata(tmp_path) -> None:
    path = tmp_path / "source"
    original = save_jpeg(path)
    client = FakeClient(valid_review())

    result = await ImageReviewer(client).review(path, observation())

    assert result == valid_review()
    assert len(client.calls) == 1
    messages, response_model = client.calls[0]
    assert response_model is ImageReview
    assert messages[0]["role"] == "system"
    content = messages[1]["content"]
    assert '"index":0' in content[0]["text"]
    assert '"label":"AJ"' in content[0]["text"]
    metadata = json.loads(content[0]["text"])
    assert metadata["label_definitions"] == {
        "AJ": "支管暗接",
        "BLOCK": "CJ/JG/ZW 合并的堵塞类",
        "PL": "破裂",
        "FS": "腐蚀",
        "BX": "变形",
    }
    assert metadata["response_schema"] == ImageReview.model_json_schema()
    schema_text = json.dumps(metadata["response_schema"], ensure_ascii=False)
    assert "proposed_label" in schema_text
    assert "revise" in schema_text
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(original).decode("ascii")
    )


@pytest.mark.asyncio
async def test_review_request_delivers_guidance_for_candidates_and_missed_classes(tmp_path):
    """Dropping domain context from the wire loses both reclassification and empty-image audit."""
    path = tmp_path / "source"
    save_jpeg(path)
    empty = ImageObservation(
        detection=DetectionBatch(
            **(observation().detection.model_dump() | {"detections": ()})
        ),
        quality=observation().quality,
    )
    client = FakeClient(valid_review(candidates=()))
    await ImageReviewer(client).review(path, empty)
    metadata = json.loads(client.calls[0][0][1]["content"][0]["text"])

    assert metadata["candidates"] == []
    assert set(metadata["label_guidance"]) == {"AJ", "BLOCK", "PL", "FS", "BX"}
    for guidance in metadata["label_guidance"].values():
        assert guidance["visual_features"]
        assert guidance["lookalikes"]
    assert metadata["label_guidance"]["BLOCK"]["source_classes"] == {
        "CJ": "沉积", "JG": "结垢", "ZW": "障碍物"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates",
    [
        (),
        (
            CandidateReview(index=0, verdict="accept", reason="first"),
            CandidateReview(index=0, verdict="reject", reason="duplicate"),
        ),
        (CandidateReview(index=1, verdict="accept", reason="out of range"),),
    ],
)
async def test_reviewer_rejects_missing_duplicate_or_out_of_range_indices(
    tmp_path, candidates
) -> None:
    path = tmp_path / "source"
    save_jpeg(path)
    client = FakeClient(valid_review(candidates=candidates))

    with pytest.raises(APIClientError) as caught:
        await ImageReviewer(client).review(path, observation())

    assert caught.value.code == "response_schema_invalid"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_reviewer_rejects_additional_box_outside_original_image(tmp_path) -> None:
    path = tmp_path / "source"
    save_jpeg(path)
    additional = AdditionalAnomaly(
        label="OTHER",
        name="未知异常",
        bbox_xyxy=(1, 2, 65, 20),
        reason="超出图像右边界",
    )
    client = FakeClient(valid_review(additional_anomalies=(additional,)))

    with pytest.raises(APIClientError) as caught:
        await ImageReviewer(client).review(path, observation())

    assert caught.value.code == "response_schema_invalid"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_reviewer_rejects_oversize_without_calling_client(tmp_path) -> None:
    path = tmp_path / "source.png"
    Image.new("RGB", (8, 8)).save(path)
    client = FakeClient(valid_review(candidates=()))

    with pytest.raises(APIClientError) as caught:
        await ImageReviewer(client, max_image_bytes=4).review(path, observation())

    assert caught.value.code == "image_too_large"
    assert caught.value.retryable is False
    assert client.calls == []


@pytest.mark.asyncio
async def test_reviewer_rejects_unsupported_image_without_calling_client(tmp_path) -> None:
    path = tmp_path / "source.gif"
    Image.new("RGB", (64, 48)).save(path, format="GIF")
    client = FakeClient(valid_review())

    with pytest.raises(APIClientError) as caught:
        await ImageReviewer(client).review(path, observation())

    assert caught.value.code == "unsupported_image"
    assert caught.value.retryable is False
    assert client.calls == []
