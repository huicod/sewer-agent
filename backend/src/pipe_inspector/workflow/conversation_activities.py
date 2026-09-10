"""Checkpointed intent parsing and bounded server-side conversation dispatch."""

import asyncio
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from pipe_inspector.agent.api_client import APIClientError, OpenAICompatibleClient
from pipe_inspector.agent.conversation import ConversationDispatcher
from pipe_inspector.agent.intent import IntentParser
from pipe_inspector.contracts.conversation import ConversationIntent
from pipe_inspector.storage.repository import MissingRecord, SubmissionConflict
from pipe_inspector.workflow.conversation_start import turn_config
from pipe_inspector.workflow.start import InspectionStarter, InspectionStartError


class TurnActivities:
    def __init__(self, settings, repository, *, parser=None, dispatcher=None):
        self.settings, self.repo = settings, repository
        self.parser = parser
        self.dispatcher = dispatcher or ConversationDispatcher(
            repository, InspectionStarter(settings, repository)
        )

    async def _enter(self, request_id, stage):
        turn = await asyncio.to_thread(self.repo.get_turn, request_id)
        await asyncio.to_thread(
            self.repo.update_turn,
            request_id,
            status="running",
            stage=stage,
            attempts=max(turn.attempts, activity.info().attempt),
        )
        return turn

    @activity.defn(name="parse_conversation_turn")
    async def parse_turn(self, run: dict) -> dict:
        request_id = UUID(run["request_id"])
        cached = await asyncio.to_thread(self.repo.load_turn_step, request_id, "intent")
        if cached is not None:
            return cached
        turn = await self._enter(request_id, "planning")
        try:
            if turn_config(self.settings) != run["config"]:
                raise APIClientError("configuration_changed", retryable=False)
            parser = self.parser
            if parser is None:
                if not self.settings.llm_configured:
                    raise APIClientError("llm_not_configured", retryable=False)
                parser = IntentParser(
                    OpenAICompatibleClient(
                        self.settings.llm_base_url,
                        self.settings.llm_model,
                        self.settings.llm_api_key,
                        timeout_seconds=run["config"]["api_timeout_seconds"],
                        enable_thinking=run["config"]["api_enable_thinking"],
                        max_output_tokens=run["config"]["api_max_output_tokens"],
                    )
                )
            turns = await asyncio.to_thread(self.repo.list_turns, turn.session_id)
            previous = [
                item
                for item in turns
                if item.status == "completed" and item.created_at < turn.created_at
            ][-5:]
            history = []
            for item in previous:
                history.extend(
                    (
                        {"role": "user", "content": item.text},
                        {"role": "assistant", "content": item.reply},
                    )
                )
            intent = await parser.parse(turn.text, history=history)
        except APIClientError as exc:
            raise ApplicationError(
                exc.code, type=exc.code, non_retryable=not exc.retryable
            ) from None
        except InspectionStartError as exc:
            raise ApplicationError(exc.code, type=exc.code, non_retryable=True) from None
        return await asyncio.to_thread(
            self.repo.save_turn_step, request_id, "intent", intent.model_dump(mode="json")
        )

    @activity.defn(name="dispatch_conversation_turn")
    async def dispatch_turn(self, run: dict) -> dict:
        request_id = UUID(run["request_id"])
        cached = await asyncio.to_thread(self.repo.load_turn_step, request_id, "dispatch")
        if cached is not None:
            return cached
        turn = await self._enter(request_id, "executing")
        raw = await asyncio.to_thread(self.repo.load_turn_step, request_id, "intent")
        intent = ConversationIntent.model_validate(raw)
        try:
            result = await self.dispatcher.handle(turn, intent)
        except (MissingRecord, SubmissionConflict):
            raise ApplicationError(
                "invalid_reference", type="invalid_reference", non_retryable=True
            ) from None
        except InspectionStartError as exc:
            raise ApplicationError(
                exc.code, type=exc.code, non_retryable=exc.status_code < 500
            ) from None
        return await asyncio.to_thread(self.repo.save_turn_step, request_id, "dispatch", result)

    @activity.defn(name="finish_conversation_turn")
    def finish_turn(self, payload: dict) -> dict:
        request_id = UUID(payload["run"]["request_id"])
        record = self.repo.get_turn(request_id)
        if record.status in {"completed", "failed"}:
            return record.model_dump(mode="json")
        error = payload.get("error")
        if error:
            changes = {
                "status": "failed",
                "stage": "finished",
                "error_code": error,
                "reply": "本条对话未完成，请检查配置或服务后重新发送。已有检测任务不会因此撤销。",
            }
        else:
            result = self.repo.load_turn_step(request_id, "dispatch")
            changes = {"status": "completed", "stage": "finished", **result}
        return self.repo.update_turn(request_id, **changes).model_dump(mode="json")
