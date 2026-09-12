"""Durable, opt-in VLM tool selection before the existing enhancement branch."""

import asyncio
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.enhancement_selection import (
    SELECTION_VERSION,
    EnhancementSelector,
    gate_proposal,
)
from pipe_inspector.contracts.enhancement import EnhancementProposal, EnhancementSelection
from pipe_inspector.contracts.inspection import ImageObservation, ImageReview
from pipe_inspector.contracts.tools import ToolCall
from pipe_inspector.workflow.enhancement_activities import (
    EnhancementActivities,
    enhancement_baseline,
)
from pipe_inspector.workflow.enhancement_config import selected_enhancement_pin


class EnhancementSelectionActivities(EnhancementActivities):
    def __init__(self, settings, repository, media, *, selector=None):
        super().__init__(settings, repository, media)
        self.selector = selector

    def _call(self, run, step, **changes):
        task = self.repo.get_task(UUID(run["task_id"]))
        return ToolCall(
            call_id=run["config"]["auto_enhancement"]["call_id"],
            task_id=run["task_id"],
            step_id=step,
            idempotency_key=f"{run['run_id']}:{step}",
            attempt=activity.info().attempt,
            tool_name="verify",
            tool_version=SELECTION_VERSION,
            resource="api",
            model_version=run["config"]["vlm_model"],
            timeout_seconds=run["config"]["api_timeout_seconds"],
            input_assets=(task.input_asset,),
            **changes,
        )

    @staticmethod
    def _result(run, checkpoint):
        selection = EnhancementSelection.model_validate(checkpoint["selection"])
        return {
            "selection": selection.model_dump(mode="json"),
            "enhancement": selected_enhancement_pin(run["config"], selection),
        }

    @activity.defn(name="select_image_enhancement")
    async def select_image_enhancement(self, run):
        task_id = UUID(run["task_id"])
        cached = await asyncio.to_thread(self.repo.load_step, task_id, "enhancement_selection")
        if cached is not None:
            return self._result(run, cached)
        auto = run["config"]["auto_enhancement"]
        await asyncio.to_thread(self._stage, run, "enhancement_selection")
        if auto.get("version") != SELECTION_VERSION:
            raise ApplicationError(
                "Selection configuration changed", type="configuration_changed", non_retryable=True
            )
        models = auto["models"]
        if not models:
            saved = await asyncio.to_thread(
                self.repo.save_step,
                task_id,
                "enhancement_selection",
                {
                    "selection": EnhancementSelection(
                        status="skipped", reason="no_available_models"
                    ).model_dump(mode="json")
                },
            )
            return self._result(run, saved)
        await asyncio.to_thread(
            self.repo.record_call,
            await asyncio.to_thread(self._call, run, "enhancement_selection", status="running"),
        )
        try:
            self._check_versions(run["config"], vlm=True)
            task = await asyncio.to_thread(self.repo.get_task, task_id)
            asset = await asyncio.to_thread(self.repo.get_asset, task.input_asset)
            observation = ImageObservation.model_validate(
                await asyncio.to_thread(self.repo.load_step, task_id, "detect")
            )
            saved = await asyncio.to_thread(self.repo.load_step, task_id, "verify")
            review = ImageReview.model_validate(saved["review"]) if saved.get("review") else None
            baseline = await asyncio.to_thread(
                enhancement_baseline, self.repo, run, task, observation, review
            )
            selector = self.selector
            if selector is None:
                if not self.settings.vlm_configured:
                    raise APIClientError("vlm_not_configured", retryable=False)
                selector = EnhancementSelector(
                    OpenAICompatibleClient(
                        self.settings.vlm_base_url,
                        self.settings.vlm_model,
                        self.settings.vlm_api_key,
                        timeout_seconds=run["config"]["api_timeout_seconds"],
                        enable_thinking=run["config"].get("api_enable_thinking"),
                        max_output_tokens=run["config"].get("api_max_output_tokens"),
                    ),
                    max_image_bytes=run["config"]["max_image_bytes"],
                )
            proposal = EnhancementProposal.model_validate(
                await selector.propose(
                    self.media.path_for(task.input_asset),
                    width=asset.width,
                    height=asset.height,
                    quality=observation.quality,
                    baseline=baseline,
                    available_kinds=list(models),
                )
            )
            selection = gate_proposal(proposal, asset, available_kinds=models)
        except APIClientError as exc:
            raise await asyncio.to_thread(
                self._error,
                run,
                "enhancement_selection",
                exc.code,
                retryable=exc.retryable,
                uncertain=exc.code in {"timeout", "network_error"},
            ) from None
        except ApplicationError as exc:
            raise await asyncio.to_thread(
                self._error, run, "enhancement_selection", exc.type or "selection_failed"
            ) from None
        except (OSError, ValueError, TypeError, KeyError):
            raise await asyncio.to_thread(
                self._error, run, "enhancement_selection", "selection_input_invalid"
            ) from None
        # Commit failures retry and see the winning choice; never overwrite it after restart.
        saved = await asyncio.to_thread(
            self.repo.save_step,
            task_id,
            "enhancement_selection",
            {"selection": selection.model_dump(mode="json")},
            await asyncio.to_thread(self._call, run, "enhancement_selection", status="succeeded"),
        )
        return self._result(run, saved)
