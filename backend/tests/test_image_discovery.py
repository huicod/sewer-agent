import base64
import json
from io import BytesIO

import pytest
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import ImageObservation, QualitySummary


def observation(width=64, height=48):
    return ImageObservation(
        detection=DetectionBatch(
            detector_id="private-detector-hint",
            model_version="private-model",
            label_space_version="five",
            width=width,
            height=height,
            detections=(
                Detection(label="PL", native_label="PL", score=0.94, bbox_xyxy=(2, 3, 20, 30)),
            ),
        ),
        quality=QualitySummary(mean_luminance=100, contrast=30, sharpness=80, adequate=True),
    )


class Client:
    def __init__(self, anomalies=()):
        self.anomalies, self.requests = anomalies, []

    async def generate(self, messages, response_model):
        self.requests.append(messages)
        return response_model.model_validate(
            dict(
                image_assessable=True,
                full_image_audited=True,
                coordinate_space="normalized_0_1000",
                anomalies=self.anomalies,
                summary="整图独立检查完成",
            )
        )


def anomaly(view=0, box=(31.25, 62.5, 312.5, 625), label="PL"):
    return dict(view_index=view, label=label, bbox_xyxy=box, name="裂纹", reason="可见材料断裂")


@pytest.mark.asyncio
async def test_independent_request_hides_detector_predictions_and_preserves_original(tmp_path):
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "image.jpg"
    Image.new("RGB", (64, 48), (80, 110, 130)).save(path)
    original = path.read_bytes()
    client = Client([anomaly()])
    review = await IndependentImageReviewer(client).review(path, observation())

    assert len(client.requests) == 1
    content = client.requests[0][1]["content"]
    metadata = json.loads(content[0]["text"])
    assert "candidates" not in metadata and "detector" not in metadata
    assert metadata["coordinate_space"] == "normalized_0_1000"
    assert "private-detector-hint" not in json.dumps(client.requests)
    assert set(metadata["label_guidance"]) == {"AJ", "BX", "FS", "PL", "BLOCK"}
    assert len(metadata["views"]) == 1
    assert (
        content[1]["image_url"]["url"]
        == "data:image/jpeg;base64," + base64.b64encode(original).decode()
    )
    assert review.candidates[0].verdict == "accept"
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_absence_in_independent_audit_is_not_a_rejection(tmp_path):
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (64, 48)).save(path)
    review = await IndependentImageReviewer(Client()).review(path, observation())
    assert review.candidates[0].verdict == "abstain"
    assert review.candidates[0].proposed_label is None


@pytest.mark.asyncio
async def test_large_image_crops_are_bounded_native_pixels_and_map_back(tmp_path):
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "large.png"
    source = Image.new("RGB", (2048, 1024), (11, 23, 47))
    source.putpixel((932, 481), (150, 160, 170))
    source.save(path)
    client = Client(
        [
            anomaly(
                view=4, box=(10 / 1126 * 1000, 20 / 563 * 1000, 40 / 1126 * 1000, 80 / 563 * 1000)
            )
        ]
    )
    review = await IndependentImageReviewer(client).review(path, observation(2048, 1024))
    content = client.requests[0][1]["content"]
    metadata = json.loads(content[0]["text"])
    assert len(metadata["views"]) == 5
    assert metadata["views"][4] == dict(view_index=4, origin_xy=[922, 461], width=1126, height=563)
    crop = Image.open(BytesIO(base64.b64decode(content[5]["image_url"]["url"].split(",")[1])))
    assert crop.size == (1126, 563)
    assert crop.getpixel((10, 20)) == (150, 160, 170)
    assert review.additional_anomalies[0].bbox_xyxy == pytest.approx((932, 481, 962, 541))


@pytest.mark.asyncio
@pytest.mark.parametrize("item", [anomaly(view=8), anomaly(box=(0, 0, 1001, 40))])
async def test_discovery_rejects_unknown_view_or_out_of_bounds_box(tmp_path, item):
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "image.png"
    Image.new("RGB", (64, 48)).save(path)
    with pytest.raises(APIClientError) as error:
        await IndependentImageReviewer(Client([item])).review(path, observation())
    assert error.value.code == "response_schema_invalid"


@pytest.mark.asyncio
async def test_request_byte_budget_keeps_original_when_crops_are_too_large(tmp_path):
    import numpy as np
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "compressed.jpg"
    Image.fromarray(
        np.random.default_rng(17).integers(0, 256, (512, 2048, 3), dtype=np.uint8)
    ).save(path, quality=10)
    client = Client()
    original = path.read_bytes()
    await IndependentImageReviewer(client, max_image_bytes=len(original)).review(
        path, observation(2048, 512)
    )
    content = client.requests[0][1]["content"]
    assert len(client.requests) == 1
    assert len(json.loads(content[0]["text"])["views"]) == 1
    assert base64.b64decode(content[1]["image_url"]["url"].split(",")[1]) == original


@pytest.mark.asyncio
async def test_large_cmyk_jpeg_can_be_audited_without_changing_source(tmp_path):
    from pipe_inspector.agent.image_discovery import IndependentImageReviewer

    path = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (2048, 512), (10, 20, 30, 0)).save(path)
    original = path.read_bytes()
    client = Client()
    await IndependentImageReviewer(client).review(path, observation(2048, 512))
    assert path.read_bytes() == original
    content = client.requests[0][1]["content"]
    assert len(json.loads(content[0]["text"])["views"]) == 5
