"""Checkpointed full-frame scan, sparse original evidence, and bounded API reviews."""

import asyncio
import shutil
from contextlib import closing
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from uuid import UUID, uuid5

from temporalio import activity
from temporalio.exceptions import ApplicationError, CancelledError

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.image_review import ImageReviewer
from pipe_inspector.agent.video_policy import decide_video, select_review_frames
from pipe_inspector.agent.video_selection import VideoSceneSelector
from pipe_inspector.contracts.assets import AssetRecord, AssetRef
from pipe_inspector.contracts.inspection import ImageReview
from pipe_inspector.contracts.tasks import TaskSnapshot
from pipe_inspector.contracts.tools import ToolCall, ToolError
from pipe_inspector.contracts.video import (
    VideoAggregationConfig,
    VideoAggregationState,
    VideoScanResult,
)
from pipe_inspector.contracts.video_selection import VIDEO_SELECTION_VERSION
from pipe_inspector.runtime_settings import settings_for_run
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.vision.video_decode import VideoDecodeError, decode_video_chunks
from pipe_inspector.vision.video_scan import scan_video
from pipe_inspector.workflow.activities import ImageActivities
from pipe_inspector.workflow.video_selection import (
    SceneBatchOutcome,
    build_candidate_pool,
    classify_scene_batches,
    select_video_review,
)

VIDEO_POLICY_VERSION = "video-review-v1"


class _FailedSceneSelector:
    def __init__(self, error: APIClientError) -> None:
        self.error = error

    async def classify(self, frames):
        raise self.error


class VideoActivities(ImageActivities):
    def __init__(
        self,
        settings,
        repository,
        media,
        *,
        detector=None,
        reviewer=None,
        scene_selector=None,
    ):
        super().__init__(settings, repository, media, detector=detector, reviewer=reviewer)
        self.scene_selector = scene_selector

    def _check_video(self, run, **options):
        self._check_versions(run["config"], **options)
        if run["config"].get("video_policy_version") != VIDEO_POLICY_VERSION:
            raise ApplicationError(
                "Video policy changed", type="configuration_changed", non_retryable=True
            )
        selection_version = run["config"].get("video_selection_version")
        if selection_version is not None and selection_version != VIDEO_SELECTION_VERSION:
            raise ApplicationError(
                "Video selection changed", type="configuration_changed", non_retryable=True
            )

    @staticmethod
    def _pulse(index=0, timestamp=0):
        activity.heartbeat(index, timestamp)
        if activity.is_cancelled():
            raise CancelledError()

    def _publish(self, task_id, stage, *, active_tool=None):
        current = self.repo.get_task(task_id).snapshot
        if current.is_terminal:
            raise ApplicationError(
                "Task already terminal", type="task_terminal", non_retryable=True
            )
        progress = {"detecting": 0.05, "aggregating": 0.8, "verifying": 0.85, "reporting": 0.95}[
            stage
        ]
        self.repo.update_snapshot(
            TaskSnapshot.model_validate(
                current.model_dump()
                | {
                    "runtime_status": "running",
                    "stage": stage,
                    "stage_progress": 0,
                    "overall_progress": max(progress, current.overall_progress),
                    "can_cancel": stage != "reporting",
                    "active_tool": active_tool
                    if active_tool is not None
                    else None
                    if stage == "reporting"
                    else "verify"
                    if stage == "verifying"
                    else "detect",
                    "updated_at": datetime.now(timezone.utc),
                    "retry_count": max(current.retry_count, activity.info().attempt - 1),
                }
            )
        )

    def _video_call(self, run, step, status, *, code=None, retryable=False, asset=None):
        detect = step == "video_scan"
        return ToolCall(
            call_id=uuid5(UUID(run["run_id"]), step),
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name="detect" if detect else "verify",
            tool_version="1",
            resource="gpu" if detect else "api",
            model_version=run["config"]["model_version" if detect else "vlm_model"],
            timeout_seconds=43200 if detect else run["config"]["api_timeout_seconds"],
            input_assets=(asset or self.repo.get_task(UUID(run["task_id"])).input_asset,),
            status=status,
            error=ToolError(code=code, retryable=retryable, message=f"视频工具未完成：{code}")
            if code
            else None,
        )

    def _initial(self, run):
        config = run["config"]
        return VideoAggregationState(
            source=self.repo.get_task(UUID(run["task_id"])).input_asset,
            config=VideoAggregationConfig.model_validate(config["video_aggregation"]),
            **{key: config[key] for key in ("detector_id", "model_version", "label_space_version")},
        )

    @activity.defn(name="scan_video")
    def scan_video(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "video_scan")
        if cached is not None:
            return {"processed_frames": cached["state"]["next_frame"]}
        self._publish(task_id, "detecting")
        self.repo.record_call(self._video_call(run, "video_scan", "running"))
        try:
            self._check_video(run, detector=True)
            state = self.repo.ensure_video_state(task_id, self._initial(run))
            result = scan_video(
                self.media.path_for(state.source),
                state.source,
                self.detector,
                initial_state=state,
                config=state.config,
                chunk_size=run["config"]["video_chunk_size"],
                max_pixels=self.media.max_pixels,
                on_progress=self._pulse,
                on_chunk=lambda observations, _: self.repo.commit_video_chunk(
                    task_id, observations, attempt=activity.info().attempt
                ),
            )
        except (ApplicationError, ValueError, SubmissionConflict) as exc:
            code = exc.type if isinstance(exc, ApplicationError) else "video_configuration_invalid"
            self.repo.record_call(self._video_call(run, "video_scan", "failed", code=code))
            raise ApplicationError(
                "Video scan configuration invalid", type=code, non_retryable=True
            ) from None
        # Storage/network exceptions and cancellation propagate, leaving a resumable head.
        saved = self.repo.finish_video_scan(task_id, error_code=result.error_code)
        self.repo.record_call(
            self._video_call(
                run,
                "video_scan",
                "failed" if saved.error_code else "succeeded",
                code=saved.error_code,
            )
        )
        return {"processed_frames": saved.state.next_frame}

    def _scan(self, task_id):
        return VideoScanResult.model_validate(self.repo.load_step(task_id, "video_scan"))

    def _observations(self, task_id, scan):
        indices = {item.frame_index for item in scan.state.audit_frames}
        indices.update(item.frame_index for event in scan.state.events for item in event.evidence)
        return {index: self.repo.get_video_frame(task_id, index) for index in sorted(indices)}

    def _save_frame(self, run, frame, source):
        ref = AssetRef(asset_id=uuid5(UUID(run["run_id"]), f"video-frame:{frame.index}"))
        path = self.media.path_for(ref)
        try:
            record = self.repo.get_asset(ref)
        except MissingRecord:
            record = None
        if record is not None and path.is_file():
            if record.parent != source or (record.width, record.height) != (
                frame.width,
                frame.height,
            ):
                raise VideoDecodeError("evidence_source_changed")
            return ref
        path.parent.mkdir(parents=True, exist_ok=True)
        # Only this deterministic derived asset; an interrupted copy is never admitted.
        with NamedTemporaryFile(dir=path.parent, prefix="frame-", delete=False) as target:
            temporary = target.name
            try:
                with frame.path.open("rb") as stream:
                    shutil.copyfileobj(stream, target)
            except BaseException:
                from pathlib import Path

                Path(temporary).unlink(missing_ok=True)
                raise
        from pathlib import Path

        Path(temporary).replace(path)
        self.repo.save_asset(
            AssetRecord(
                ref=ref,
                kind="frame",
                media_type="image/png",
                byte_size=path.stat().st_size,
                width=frame.width,
                height=frame.height,
                parent=source,
            )
        )
        return ref

    @activity.defn(name="prepare_video")
    def prepare_video(self, run: dict) -> dict:
        task_id = UUID(run["task_id"])
        cached = self.repo.load_step(task_id, "video_evidence")
        if cached is not None:
            return {"review_frames": cached["review_frames"]}
        self._check_video(run)
        self._publish(task_id, "aggregating")
        scan = self._scan(task_id)
        observations = self._observations(task_id, scan)
        config = run["config"]
        selection_enabled = config.get("video_selection_version") == VIDEO_SELECTION_VERSION
        if selection_enabled:
            pool_frames = build_candidate_pool(
                scan,
                observations,
                force_review=config["force_review"],
                budget=config["video_review_budget"],
            )
            review_frames = ()
        else:
            # No selection pin means an old admitted run: preserve its original ordering.
            pool_frames = ()
            review_frames = select_review_frames(
                scan,
                observations,
                force_review=config["force_review"],
                budget=config["video_review_budget"],
            )
        # Persist event evidence too, even where a required review cannot fit the API budget.
        selected = set(review_frames)
        selected.update(pool_frames)
        selected.update(item.frame_index for event in scan.state.events for item in event.evidence)
        if config.get("open_scan"):
            # Reuse this decode pass even when VLM reviews consumed a smaller reserved budget.
            negative_frames = [
                item.frame_index
                for item in scan.state.audit_frames
                if observations[item.frame_index].quality.adequate
                and not observations[item.frame_index].detection.detections
            ]
            selected.update(negative_frames[: config["open_scan"]["max_frames"]])
        artifacts = []
        if selected:
            with closing(
                decode_video_chunks(
                    self.media.path_for(scan.state.source),
                    start_frame=min(selected),
                    selected_frames=frozenset(selected),
                    max_pixels=self.media.max_pixels,
                    on_progress=self._pulse,
                )
            ) as chunks:
                for frames in chunks:
                    for frame in frames:
                        if frame.index not in selected:
                            continue
                        expected = observations[frame.index]
                        if (frame.timestamp_seconds, frame.width, frame.height) != (
                            expected.timestamp_seconds,
                            expected.detection.width,
                            expected.detection.height,
                        ):
                            raise VideoDecodeError("evidence_source_changed")
                        ref = self._save_frame(run, frame, scan.state.source)
                        artifacts.append(
                            {
                                "frame_index": frame.index,
                                "timestamp_seconds": frame.timestamp_seconds,
                                "asset": ref.model_dump(mode="json"),
                            }
                        )
                    if len(artifacts) == len(selected):
                        break
            if len(artifacts) != len(selected):
                raise VideoDecodeError("evidence_incomplete")
        selection = None
        if selection_enabled:
            selector = self.scene_selector
            should_screen = (
                len(pool_frames) >= 4
                and config["video_review_budget"] >= 2
                and not config["force_review"]
            )
            if should_screen:
                artifact_by_index = {
                    item["frame_index"]: AssetRef.model_validate(item["asset"])
                    for item in artifacts
                }
                batch_frames = tuple(
                    (
                        index,
                        artifact_by_index[index],
                        self.media.path_for(artifact_by_index[index]),
                    )
                    for index in pool_frames
                )

                def selector_for_request():
                    nonlocal selector
                    self._check_video(run, vlm=True)
                    self._publish(task_id, "verifying", active_tool="video_scene")
                    if selector is None:
                        settings = settings_for_run(self.settings, run["config"])
                        if not settings.vlm_configured:
                            selector = _FailedSceneSelector(
                                APIClientError("vlm_not_configured", retryable=False)
                            )
                        else:
                            selector = VideoSceneSelector(
                                OpenAICompatibleClient(
                                    settings.vlm_base_url,
                                    settings.vlm_model,
                                    settings.vlm_api_key,
                                    timeout_seconds=config["api_timeout_seconds"],
                                    enable_thinking=config.get("api_enable_thinking"),
                                    max_output_tokens=min(
                                        config.get("api_max_output_tokens") or 4096,
                                        4096,
                                    ),
                                ),
                                max_image_bytes=config["max_image_bytes"],
                            )
                    return selector

                scene = asyncio.run(
                    classify_scene_batches(
                        self.repo,
                        run,
                        scan.state.source,
                        batch_frames,
                        selector,
                        budget=config["video_review_budget"],
                        force_review=config["force_review"],
                        heartbeat=self._pulse,
                        before_request=selector_for_request,
                        attempt=activity.info().attempt,
                    )
                )
            else:
                scene = SceneBatchOutcome((), 0, ())
            selection = select_video_review(
                scan,
                observations,
                scene.assessments,
                force_review=config["force_review"],
                budget=config["video_review_budget"],
                scene_calls=scene.scene_calls,
                scene_errors=scene.scene_errors,
            )
            review_frames = selection.review_frames
        payload = {"frames": artifacts, "review_frames": list(review_frames)}
        if selection is not None:
            payload["selection"] = selection.model_dump(mode="json")
        saved = self.repo.save_step(task_id, "video_evidence", payload)
        return {"review_frames": saved["review_frames"]}

    @activity.defn(name="verify_video_frame")
    async def verify_video_frame(self, payload: dict) -> dict:
        run, index = payload["run"], payload["frame_index"]
        task_id, step = UUID(run["task_id"]), f"video_verify:{index}"
        cached = await asyncio.to_thread(self.repo.load_step, task_id, step)
        if cached is not None:
            return {"frame_index": index}
        evidence = await asyncio.to_thread(self.repo.load_step, task_id, "video_evidence")
        if evidence is None or index not in evidence["review_frames"]:
            raise ApplicationError(
                "Frame is outside pinned review selection",
                type="review_not_selected",
                non_retryable=True,
            )
        asset = next(
            AssetRef.model_validate(item["asset"])
            for item in evidence["frames"]
            if item["frame_index"] == index
        )
        await asyncio.to_thread(self._publish, task_id, "verifying")
        await asyncio.to_thread(
            self.repo.record_call, self._video_call(run, step, "running", asset=asset)
        )
        try:
            self._check_video(run, vlm=True)
            reviewer = self.reviewer
            if reviewer is None:
                settings = settings_for_run(self.settings, run["config"])
                if not settings.vlm_configured:
                    raise APIClientError("vlm_not_configured", retryable=False)
                reviewer = ImageReviewer(
                    OpenAICompatibleClient(
                        settings.vlm_base_url,
                        settings.vlm_model,
                        settings.vlm_api_key,
                        timeout_seconds=run["config"]["api_timeout_seconds"],
                        enable_thinking=run["config"].get("api_enable_thinking"),
                        max_output_tokens=run["config"].get("api_max_output_tokens"),
                    ),
                    max_image_bytes=run["config"]["max_image_bytes"],
                )
            observation = await asyncio.to_thread(self.repo.get_video_frame, task_id, index)
            review = await reviewer.review(self.media.path_for(asset), observation)
        except (APIClientError, ApplicationError, OSError, ValueError) as exc:
            code = (
                exc.code
                if isinstance(exc, APIClientError)
                else (exc.type if isinstance(exc, ApplicationError) else "review_input_invalid")
            )
            retryable = isinstance(exc, APIClientError) and exc.retryable
            await asyncio.to_thread(
                self.repo.record_call,
                self._video_call(
                    run,
                    step,
                    "uncertain" if code in {"timeout", "network_error"} else "failed",
                    code=code,
                    retryable=retryable,
                    asset=asset,
                ),
            )
            raise ApplicationError(
                "Video frame review failed", type=code, non_retryable=not retryable
            ) from None
        await asyncio.to_thread(
            self.repo.save_step,
            task_id,
            step,
            {"review": review.model_dump(mode="json")},
            self._video_call(run, step, "succeeded", asset=asset),
        )
        return {"frame_index": index}

    @staticmethod
    def _summary(snapshot):
        return {
            "task_id": str(snapshot.task_id),
            "runtime_status": snapshot.runtime_status.value,
            "inspection_result": snapshot.inspection_result.value,
        }

    @activity.defn(name="finish_video")
    def finish_video(self, payload: dict) -> dict:
        run, error = payload["run"], payload.get("error")
        task_id = UUID(run["task_id"])
        task = self.repo.get_task(task_id)
        if task.snapshot.is_terminal:
            return self._summary(task.snapshot)
        cached = self.repo.load_step(task_id, "final")
        if cached is not None:
            snapshot = TaskSnapshot.model_validate(cached)
            self.repo.update_snapshot(snapshot)
            return self._summary(snapshot)
        self._publish(task_id, "reporting")
        task = self.repo.get_task(task_id)
        cancelled = bool(payload.get("cancelled"))
        self._mark_running_calls_uncertain(
            task_id, "cancelled" if cancelled else error or "activity_interrupted"
        )
        scan_raw = self.repo.load_step(task_id, "video_scan")
        if scan_raw is None:
            self.repo.ensure_video_state(task_id, self._initial(run))
            scan = self.repo.finish_video_scan(
                task_id, error_code="cancelled" if cancelled else error or "scan_incomplete"
            )
        else:
            scan = VideoScanResult.model_validate(scan_raw)
        observations = self._observations(task_id, scan)
        evidence = self.repo.load_step(task_id, "video_evidence") or {"frames": []}
        assets = {
            item["frame_index"]: AssetRef.model_validate(item["asset"])
            for item in evidence["frames"]
        }
        reviews = {}
        errors = {int(key): value for key, value in payload.get("review_errors", {}).items()}
        for index in observations:
            saved = self.repo.load_step(task_id, f"video_verify:{index}")
            if saved is not None:
                reviews[index] = ImageReview.model_validate(saved["review"])
        configuration_changed = False
        try:
            self._check_video(run)
        except ApplicationError:
            configuration_changed = True
            error = "configuration_changed"
            reviews = {}
        decision = decide_video(
            task_id,
            scan,
            observations,
            assets,
            reviews,
            errors,
            force_review=run["config"]["force_review"] or configuration_changed,
        )
        from pipe_inspector.workflow.open_scan_activities import apply_open_results

        decision = apply_open_results(self.repo, run, decision)
        partial = bool(
            error or errors or decision.needs_review or decision.inspection_result == "inconclusive"
        )
        result = decision.inspection_result
        if cancelled and result == "normal":
            result = "inconclusive"
        reasons = list(decision.reasons)
        if error:
            reasons.append(error)
        snapshot = TaskSnapshot.model_validate(
            task.snapshot.model_dump()
            | {
                "runtime_status": "cancelled"
                if cancelled
                else (
                    "failed"
                    if scan.state.next_frame == 0
                    else "partial"
                    if partial
                    else "completed"
                ),
                "inspection_result": result,
                "stage": "finished",
                "stage_progress": 1,
                "overall_progress": 1,
                "can_cancel": False,
                "active_tool": None,
                "findings": decision.findings,
                "coverage": decision.coverage,
                "processed_frames": scan.state.next_frame,
                "updated_at": datetime.now(timezone.utc),
                "waiting_reason": "；".join(reasons) or None,
            }
        )
        if (
            run["config"].get("human_review_version") == "human-review-v1"
            and snapshot.runtime_status == "partial"
            and not configuration_changed
        ):
            snapshot = TaskSnapshot.model_validate(
                snapshot.model_dump()
                | {
                    "runtime_status": "waiting_review",
                    "stage": "human_review",
                    "overall_progress": 0.95,
                    "can_cancel": True,
                    "waiting_reason": snapshot.waiting_reason or "请人工审核检测结果",
                }
            )
        saved = self.repo.save_step(task_id, "final", snapshot.model_dump(mode="json"))
        snapshot = TaskSnapshot.model_validate(saved)
        self.repo.update_snapshot(snapshot)
        return self._summary(snapshot)
