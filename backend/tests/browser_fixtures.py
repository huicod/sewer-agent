"""Deterministic media and model substitutes for browser acceptance only."""

import asyncio
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
from PIL import Image
from pipe_inspector.contracts.assets import AssetKind
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.contracts.detection import Detection, DetectionBatch
from pipe_inspector.contracts.inspection import CandidateReview, ImageObservation, ImageReview
from pipe_inspector.storage.repository import SubmissionConflict
from pipe_inspector.vision.detector import LABEL_SPACE_VERSION, DetectorCapabilities
from pipe_inspector.workflow.start import InspectionStarter, run_config, video_run_config

_UUID_TOKEN = re.compile(
    r"(?<![0-9a-zA-Z_])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-zA-Z_])"
)


def prepare_output(environ: Mapping[str, str] | None = None) -> Path:
    """Validate the destructive boundary before creating this run's output root."""

    values = os.environ if environ is None else environ
    if values.get("SEWER_RUN_BROWSER_TEST") != "1":
        raise RuntimeError("browser server requires SEWER_RUN_BROWSER_TEST=1")
    raw = values.get("SEWER_BROWSER_OUTPUT", "")
    output = Path(raw)
    if not raw or not output.is_absolute():
        raise RuntimeError("SEWER_BROWSER_OUTPUT must be an absolute new directory")
    if output.exists():
        raise RuntimeError("SEWER_BROWSER_OUTPUT must not already exist")
    output.mkdir(parents=True, exist_ok=False)
    return output


def create_media_fixtures(output: Path) -> tuple[Path, Path]:
    """Create the fixed 64px image and six-frame MP4 consumed through real uploads."""

    fixtures = output / "fixtures"
    fixtures.mkdir(exist_ok=False)
    (output / "assets").mkdir(exist_ok=False)
    image_path = fixtures / "image.png"
    video_path = fixtures / "video.mp4"
    pixels = np.random.default_rng(1501).integers(
        40, 211, size=(64, 64, 3), dtype=np.uint8
    )
    Image.fromarray(pixels).save(image_path, format="PNG")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=64x64:rate=5:duration=1.2",
                "-frames:v",
                "6",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-n",
                str(video_path),
            ],
            check=True,
            timeout=20,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for browser acceptance fixtures") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("browser acceptance video generation timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("browser acceptance video generation failed") from exc
    return image_path, video_path


class BrowserDetector:
    """One deterministic original-coordinate candidate; never loads a model."""

    detector_id = "browser-fixture-detector"
    model_version = "browser-fixture-detector-v1"
    label_space_version = LABEL_SPACE_VERSION
    capabilities = DetectorCapabilities(supports_batch=False)

    def __init__(self) -> None:
        self.calls = 0

    def predict_image(self, path: str | Path) -> DetectionBatch:
        with Image.open(path) as image:
            width, height = image.size
        if (width, height) != (64, 64):
            raise ValueError("browser fixture detector accepts only 64x64 images")
        self.calls += 1
        return DetectionBatch(
            detector_id=self.detector_id,
            model_version=self.model_version,
            label_space_version=self.label_space_version,
            width=width,
            height=height,
            detections=(
                Detection(
                    label="AJ",
                    native_label="AJ",
                    score=0.65,
                    bbox_xyxy=(10, 10, 40, 40),
                ),
            ),
        )


class BrowserReviewer:
    """Deterministically abstain so both media workflows reach human review."""

    def __init__(self) -> None:
        self.calls = 0

    async def review(
        self, path: str | Path, observation: ImageObservation
    ) -> ImageReview:
        if not Path(path).is_file():
            raise FileNotFoundError(path)
        self.calls += 1
        return ImageReview(
            image_assessable=True,
            full_image_audited=True,
            candidates=tuple(
                CandidateReview(
                    index=index,
                    verdict="abstain",
                    reason="browser acceptance fixture requires human confirmation",
                )
                for index, _ in enumerate(observation.detection.detections)
            ),
            additional_anomalies=(),
            summary="受控浏览器验收替身选择弃权；该结果不是模型准确率证据。",
        )


class BrowserIntentParser:
    """Small allowlisted parser for acceptance journeys, never arbitrary tool choice."""

    def __init__(self) -> None:
        self.calls = 0

    async def parse(
        self,
        text: str,
        history: Sequence[Mapping[str, Any]] = (),
    ) -> ConversationIntent:
        del history
        self.calls += 1
        lowered = text.casefold().strip()
        if any(token in lowered for token in ("比较", "对比", "compare")):
            match = _UUID_TOKEN.search(text)
            return ConversationIntent(
                kind="compare",
                actions=("compare",),
                comparison_reference_task_id=(UUID(match.group()) if match else None),
                message="将比较两份已保存检查，不执行检测模型。",
            )
        if lowered.startswith(("导出报告", "生成报告", "报告", "report")):
            return ConversationIntent(kind="report", message="将读取已保存报告。")
        if any(
            token in lowered
            for token in ("为什么", "状态", "已有结果", "询问", "ask", "question")
        ):
            return ConversationIntent(kind="ask", message="将读取已保存检查结果。")
        return ConversationIntent(
            kind="inspect",
            actions=("verify",),
            message="将执行受控图片或视频检查并进入人工审核。",
        )


class BrowserInspectionStarter(InspectionStarter):
    """Pin substitute detector metadata before delegating to the real starter."""

    def __init__(self, settings, repository, detector: BrowserDetector, **kwargs) -> None:
        super().__init__(settings, repository, **kwargs)
        self.detector = detector

    async def start(self, task_id, *, force_review=False, plan=None):
        record = await asyncio.to_thread(self.repo.get_task, task_id)
        asset = await asyncio.to_thread(self.repo.get_asset, record.input_asset)
        run = await asyncio.to_thread(self.repo.get_run, task_id)
        if run is None and not record.snapshot.is_terminal:
            config = (
                video_run_config(
                    self.settings,
                    detector=self.detector,
                    force_review=force_review,
                    plan=plan,
                )
                if asset.kind == AssetKind.VIDEO
                else run_config(
                    self.settings,
                    detector=self.detector,
                    force_review=force_review,
                    plan=plan,
                    inspection_mode=record.inspection_mode,
                )
            )
            try:
                await asyncio.to_thread(
                    self.repo.ensure_run, task_id, config
                )
            except SubmissionConflict:
                if await asyncio.to_thread(self.repo.get_run, task_id) is None:
                    raise
        return await super().start(task_id, force_review=force_review, plan=plan)
