"""Explicitly gated production-adapter assembly and evidence for live browser tests."""

from __future__ import annotations

import contextvars
import json
import shutil
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import httpx
from PIL import Image
from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pydantic import BaseModel, SecretStr

LIVE_API_LIMIT = 8
_IMAGE_OMITTED = "[image omitted]"
_CURRENT_CALL: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "browser_live_call", default=None
)
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)
TransportFactory = Callable[[], httpx.AsyncBaseTransport]


@dataclass(frozen=True, slots=True)
class LiveAdmission:
    source: Path


def load_live_admission(environ: Mapping[str, str]) -> LiveAdmission | None:
    """Return a live admission only when both paid-test switches are exact."""

    browser = environ.get("SEWER_RUN_BROWSER_LIVE")
    paid = environ.get("SEWER_RUN_LIVE_API")
    if browser in {None, "0"} and paid in {None, "0"}:
        return None
    if browser != "1" or paid != "1":
        raise RuntimeError("live browser requires both explicit live API gates")
    raw_source = environ.get("SEWER_LIVE_IMAGE", "")
    source = Path(raw_source)
    if not raw_source or not source.is_absolute() or not source.is_file():
        raise RuntimeError("live browser requires an existing absolute SEWER_LIVE_IMAGE")
    try:
        with Image.open(source) as image:
            if image.format != "JPEG" or image.width <= 0 or image.height <= 0:
                raise RuntimeError("live browser source must be a valid JPEG image")
            if image.size != (720, 576):
                raise RuntimeError("fixed live browser ROIs require the documented 720x576 image")
            image.verify()
    except (OSError, ValueError):
        raise RuntimeError("live browser source must be a valid JPEG image") from None
    return LiveAdmission(source=source)


def copy_live_fixture(source: Path, output: Path) -> dict[str, object]:
    """Copy exact authorized input bytes to the fixed browser fixture name."""

    fixtures = output / "fixtures"
    fixtures.mkdir(exist_ok=False)
    (output / "assets").mkdir(exist_ok=False)
    target = fixtures / "image.jpg"
    shutil.copyfile(source, target)
    try:
        with Image.open(target) as image:
            if image.format != "JPEG":
                raise RuntimeError("live browser source must be JPEG")
            width, height = image.size
            image.verify()
    except (OSError, ValueError):
        raise RuntimeError("live browser source is not readable") from None
    return {
        "fixture": "fixtures/image.jpg",
        "media_type": "image/jpeg",
        "byte_size": target.stat().st_size,
        "width": width,
        "height": height,
    }


def _safe_value(value: Any, *, key: str | None = None) -> Any:
    if key and key.casefold() in {"authorization", "api_key", "image_url"}:
        return _IMAGE_OMITTED if key.casefold() == "image_url" else "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _safe_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str) and value.casefold().startswith("data:image/"):
        return _IMAGE_OMITTED
    return value


def _usage_from(content: bytes) -> dict[str, int] | None:
    try:
        usage = json.loads(content).get("usage")
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(usage, Mapping):
        return None
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    result: dict[str, int] = {}
    for name in names:
        value = usage.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        result[name] = value
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, Mapping) else None
    if isinstance(cached, int) and not isinstance(cached, bool) and cached >= 0:
        result["cached_tokens"] = cached
    return result or None


class _RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, recorder: LiveExecutionRecorder, factory: TransportFactory) -> None:
        self._recorder = recorder
        self._factory = factory

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        downstream = self._factory()
        try:
            response = await downstream.handle_async_request(request)
            content = await response.aread()
            call_id = _CURRENT_CALL.get()
            if call_id is not None:
                self._recorder.observe_response(
                    call_id, status_code=response.status_code, usage=_usage_from(content)
                )
            return response
        finally:
            await downstream.aclose()


class _RecordingClient:
    def __init__(
        self,
        delegate: OpenAICompatibleClient,
        recorder: LiveExecutionRecorder,
        *,
        role: str,
        model: str,
    ) -> None:
        self._delegate = delegate
        self._recorder = recorder
        self._role = role
        self._model = model

    async def generate(
        self,
        messages: Sequence[Mapping[str, Any]],
        response_model: type[_ResponseModel],
    ) -> _ResponseModel:
        call_id = self._recorder.begin(self._role, self._model, messages)
        token = _CURRENT_CALL.set(call_id)
        try:
            result = await self._delegate.generate(messages, response_model)
        except APIClientError as exc:
            self._recorder.finish(call_id, status="failed", error_code=exc.code)
            raise
        except BaseException as exc:
            self._recorder.finish(
                call_id, status="failed", error_code=type(exc).__name__.casefold()
            )
            raise
        else:
            self._recorder.finish(
                call_id,
                status="succeeded",
                result=_safe_value(result.model_dump(mode="json")),
            )
            return result
        finally:
            _CURRENT_CALL.reset(token)


class LiveExecutionRecorder:
    """Shared physical-call budget and safe serializable execution ledger."""

    def __init__(self, limit: int = LIVE_API_LIMIT) -> None:
        if limit <= 0:
            raise ValueError("live API limit must be positive")
        self.limit = limit
        self._calls: list[dict[str, Any]] = []
        self._started: dict[int, float] = {}
        self._budget_rejections = 0
        self._lock = threading.Lock()

    def client(
        self,
        *,
        role: str,
        base_url: str,
        model: str,
        api_key: SecretStr,
        timeout_seconds: float,
        enable_thinking: bool | None = None,
        max_output_tokens: int | None = None,
        transport_factory: TransportFactory | None = None,
    ) -> _RecordingClient:
        factory = transport_factory or httpx.AsyncHTTPTransport
        delegate = OpenAICompatibleClient(
            base_url,
            model,
            api_key,
            timeout_seconds=timeout_seconds,
            transport=_RecordingTransport(self, factory),
            enable_thinking=enable_thinking,
            max_output_tokens=max_output_tokens,
        )
        return _RecordingClient(delegate, self, role=role, model=model)

    def begin(
        self, role: str, model: str, messages: Sequence[Mapping[str, Any]]
    ) -> int:
        with self._lock:
            if len(self._calls) >= self.limit:
                self._budget_rejections += 1
                raise APIClientError("live_api_budget_exhausted", retryable=False)
            call_id = len(self._calls) + 1
            self._started[call_id] = time.perf_counter()
            self._calls.append(
                {
                    "sequence": call_id,
                    "role": role,
                    "model": model,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                    "elapsed_seconds": None,
                    "request": {"messages": _safe_value(messages)},
                    "result": None,
                    "usage": None,
                    "http_status": None,
                    "error_code": None,
                }
            )
            return call_id

    def observe_response(
        self, call_id: int, *, status_code: int, usage: dict[str, int] | None
    ) -> None:
        with self._lock:
            call = self._calls[call_id - 1]
            call["http_status"] = status_code
            call["usage"] = usage

    def finish(
        self,
        call_id: int,
        *,
        status: str,
        result: Any = None,
        error_code: str | None = None,
    ) -> None:
        with self._lock:
            call = self._calls[call_id - 1]
            call["status"] = status
            call["elapsed_seconds"] = time.perf_counter() - self._started.pop(call_id)
            call["result"] = result
            call["error_code"] = error_code

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = json.loads(json.dumps(self._calls))
            rejections = self._budget_rejections
        return {
            "api_request_limit": self.limit,
            "provider_calls": calls,
            "failed_calls": sum(call["status"] == "failed" for call in calls),
            "usage_unknown_calls": sum(call["usage"] is None for call in calls),
            "budget_rejections": rejections,
        }


class GpuExecutionRecorder:
    """Measure one serial GPU adapter call without retaining tensors or media."""

    def __init__(self) -> None:
        self._calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def measure(self, operation: str, device: str, function, *args, **kwargs):
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("live browser GPU is unavailable")
        cuda_device = int(device) if isinstance(device, str) and device.isdecimal() else device
        started = time.perf_counter()
        torch.cuda.synchronize(cuda_device)
        torch.cuda.reset_peak_memory_stats(cuda_device)
        status = "succeeded"
        try:
            return function(*args, **kwargs)
        except BaseException:
            status = "failed"
            raise
        finally:
            torch.cuda.synchronize(cuda_device)
            item = {
                "operation": operation,
                "status": status,
                "elapsed_seconds": time.perf_counter() - started,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(cuda_device),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(cuda_device),
            }
            with self._lock:
                self._calls.append(item)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            calls = json.loads(json.dumps(self._calls))
        return {
            "serial_calls": calls,
            "peak_allocated_bytes": max(
                (item["peak_allocated_bytes"] for item in calls), default=None
            ),
            "peak_reserved_bytes": max(
                (item["peak_reserved_bytes"] for item in calls), default=None
            ),
        }


class _MeasuredDetector:
    def __init__(self, delegate, recorder: GpuExecutionRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def predict_image(self, path):
        return self._recorder.measure(
            "detect_image", self._delegate.device, self._delegate.predict_image, path
        )

    def predict_batch(self, paths):
        return self._recorder.measure(
            "detect_batch", self._delegate.device, self._delegate.predict_batch, paths
        )


class _MeasuredSegmenter:
    def __init__(self, delegate, recorder: GpuExecutionRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def segment_image(self, image, prompt):
        return self._recorder.measure(
            "segment_image", self._delegate.device, self._delegate.segment_image, image, prompt
        )


class _MeasuredDepth:
    larger_is = "nearer"

    def __init__(self, delegate, recorder: GpuExecutionRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def predict_depth(self, image):
        return self._recorder.measure(
            "relative_depth", self._delegate.device, self._delegate.predict_depth, image
        )


class _MeasuredEnhancement:
    def __init__(self, delegate, recorder: GpuExecutionRecorder) -> None:
        self._delegate = delegate
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def enhance_image(self, image):
        return self._recorder.measure(
            "enhance_image", self._delegate.device, self._delegate.enhance_image, image
        )


@dataclass(slots=True)
class LiveComponents:
    recorder: LiveExecutionRecorder
    gpu: GpuExecutionRecorder
    detector: Any
    reviewer: Any
    parser: Any
    starter: Any
    turns: Any
    images: Any
    videos: Any
    enhancements: Any
    enhancement_selection: Any
    geometry: Any
    supplements: Any
    models: dict[str, str | None]


def _role_client(settings, recorder: LiveExecutionRecorder, role: str, *, llm: bool = False):
    prefix = "llm" if llm else "vlm"
    base_url = getattr(settings, f"{prefix}_base_url")
    model = getattr(settings, f"{prefix}_model")
    api_key = getattr(settings, f"{prefix}_api_key")
    if not base_url or not model or not isinstance(api_key, SecretStr):
        raise RuntimeError(f"live browser {prefix} is not configured")
    return recorder.client(
        role=role,
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout_seconds=settings.api_timeout_seconds,
        enable_thinking=settings.api_enable_thinking,
        max_output_tokens=settings.api_max_output_tokens,
    )


def build_live_components(settings, repository, media) -> LiveComponents:
    """Construct only existing production adapters, with test-only measurement wrappers."""

    from pipe_inspector.agent.conversation import ConversationDispatcher
    from pipe_inspector.agent.enhancement_review import EnhancementReviewer
    from pipe_inspector.agent.enhancement_selection import EnhancementSelector
    from pipe_inspector.agent.image_review import ImageReviewer
    from pipe_inspector.agent.intent import IntentParser
    from pipe_inspector.vision.detector import UltralyticsDetector
    from pipe_inspector.vision.enhancement import ENHANCEMENT_MODELS
    from pipe_inspector.vision.geometry_models import (
        DEPTH_ANYTHING_V2_REVISION,
        SAM2_REVISION,
    )
    from pipe_inspector.workflow.activities import ImageActivities
    from pipe_inspector.workflow.conversation_activities import TurnActivities
    from pipe_inspector.workflow.enhancement_activities import EnhancementActivities
    from pipe_inspector.workflow.enhancement_selection import EnhancementSelectionActivities
    from pipe_inspector.workflow.geometry_activities import GeometryActivities
    from pipe_inspector.workflow.start import InspectionStarter
    from pipe_inspector.workflow.supplement_activities import SupplementActivities
    from pipe_inspector.workflow.video_activities import VideoActivities

    recorder = LiveExecutionRecorder()
    gpu = GpuExecutionRecorder()
    detector = _MeasuredDetector(
        UltralyticsDetector(settings.detector_path, device=settings.detector_device), gpu
    )
    image_reviewer = ImageReviewer(
        _role_client(settings, recorder, "image_review"),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    from pipe_inspector.agent.video_selection import VideoSceneSelector

    video_scene_selector = VideoSceneSelector(
        _role_client(settings, recorder, "video_scene"),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    parser = IntentParser(_role_client(settings, recorder, "intent", llm=True))
    enhancement_reviewer = EnhancementReviewer(
        _role_client(settings, recorder, "enhancement_review"),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    enhancement_selector = EnhancementSelector(
        _role_client(settings, recorder, "enhancement_selection"),
        max_image_bytes=settings.vlm_max_image_bytes,
    )
    starter = InspectionStarter(settings, repository)
    turns = TurnActivities(
        settings,
        repository,
        parser=parser,
        dispatcher=ConversationDispatcher(repository, starter),
    )
    images = ImageActivities(
        settings, repository, media, detector=detector, reviewer=image_reviewer
    )
    videos = VideoActivities(
        settings, repository, media, detector=detector, reviewer=image_reviewer,
        scene_selector=video_scene_selector,
    )

    def enhancement_factory(kind, checkpoint, device="cuda:0"):
        from pipe_inspector.vision.enhancement_models import LocalEnhancementModel

        return _MeasuredEnhancement(
            LocalEnhancementModel(kind, checkpoint, device=device), gpu
        )

    def segmenter_factory(checkpoint, device="cuda:0"):
        from pipe_inspector.vision.geometry_models import LocalSegmenter

        return _MeasuredSegmenter(LocalSegmenter(checkpoint, device=device), gpu)

    def depth_factory(checkpoint, device="cuda:0"):
        from pipe_inspector.vision.geometry_models import LocalRelativeDepth

        return _MeasuredDepth(LocalRelativeDepth(checkpoint, device=device), gpu)

    enhancements = EnhancementActivities(
        settings,
        repository,
        media,
        model_factory=enhancement_factory,
        reviewer=enhancement_reviewer,
    )
    enhancement_selection = EnhancementSelectionActivities(
        settings, repository, media, selector=enhancement_selector
    )
    geometry = GeometryActivities(
        settings,
        repository,
        media,
        segmenter_factory=segmenter_factory,
        depth_factory=depth_factory,
    )
    supplements = SupplementActivities(
        settings, repository, media, detector=detector, reviewer=image_reviewer
    )
    return LiveComponents(
        recorder=recorder,
        gpu=gpu,
        detector=detector,
        reviewer=image_reviewer,
        parser=parser,
        starter=starter,
        turns=turns,
        images=images,
        videos=videos,
        enhancements=enhancements,
        enhancement_selection=enhancement_selection,
        geometry=geometry,
        supplements=supplements,
        models={
            "detector": detector.model_version,
            "intent": settings.llm_model,
            "review": settings.vlm_model,
            "sam": f"facebook/sam2.1-hiera-large@{SAM2_REVISION}",
            "depth": (
                "depth-anything/Depth-Anything-V2-Small-hf@"
                f"{DEPTH_ANYTHING_V2_REVISION}"
            ),
            "super_resolution": ENHANCEMENT_MODELS["super_resolution"][1],
        },
    )


def write_live_execution(
    output: Path,
    repository,
    components: LiveComponents,
    *,
    source: Mapping[str, object],
) -> None:
    """Persist safe global metrics plus exact per-task tool/checkpoint evidence."""

    task_dir = output / "live-tasks"
    task_dir.mkdir(exist_ok=True)
    with repository._connect() as connection:
        task_rows = connection.execute(
            "SELECT task_id, record FROM "
            + repository._table("sewer_tasks").as_string(connection)
            + " ORDER BY created_at, task_id"
        ).fetchall()
        checkpoints = connection.execute(
            "SELECT task_id, step, payload FROM "
            + repository._table("sewer_step_checkpoints").as_string(connection)
            + " ORDER BY task_id, step"
        ).fetchall()
        calls = connection.execute(
            "SELECT task_id, record FROM "
            + repository._table("sewer_tool_calls").as_string(connection)
            + " ORDER BY task_id, call_id, attempt"
        ).fetchall()
    by_task: dict[str, dict[str, Any]] = {
        str(task_id): {"task": record, "tool_calls": [], "steps": {}}
        for task_id, record in task_rows
    }
    for task_id, step, payload in checkpoints:
        by_task[str(task_id)]["steps"][step] = payload
    for task_id, record in calls:
        by_task[str(task_id)]["tool_calls"].append(record)
    for task_id, payload in by_task.items():
        (task_dir / f"{task_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    payload = {
        "kind": "live_browser_acceptance",
        "source": dict(source),
        "models": components.models,
        **components.recorder.snapshot(),
        "gpu": components.gpu.snapshot(),
        "task_evidence": [f"live-tasks/{task_id}.json" for task_id in by_task],
        "billing_amount": None,
        "billing_note": "Provider usage is recorded when returned; price calculation is external.",
    }
    (output / "live-execution.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
