"""Persist conversation admission before repeat-safe Temporal submission."""

import asyncio

import httpx
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError

from pipe_inspector.contracts.turns import TurnInput, TurnSubmission
from pipe_inspector.workflow.start import InspectionStartError

CONVERSATION_VERSION = "conversation-v3-enhancement"


def turn_config(settings):
    if settings.llm_base_url:
        try:
            url = httpx.URL(settings.llm_base_url)
            valid = (
                url.scheme in {"http", "https"}
                and bool(url.host)
                and not url.username
                and not url.password
                and not url.query
                and not url.fragment
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise InspectionStartError("invalid_llm_configuration", "LLM Base URL 配置无效")
    return {
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url,
        "api_timeout_seconds": settings.api_timeout_seconds,
        "api_enable_thinking": settings.api_enable_thinking,
        "api_max_output_tokens": settings.api_max_output_tokens,
        "prompt_version": CONVERSATION_VERSION,
        "task_queue": settings.task_queue,
    }


class ConversationStarter:
    def __init__(self, settings, repository, *, connect=Client.connect):
        self.settings, self.repo, self.connect = settings, repository, connect

    async def submit(self, session_id, body: TurnInput):
        submission = TurnSubmission(
            session_id=session_id, **body.model_dump(include=set(TurnInput.model_fields))
        )
        record = await asyncio.to_thread(
            self.repo.admit_turn, submission, turn_config(self.settings)
        )
        if record.status in {"completed", "failed"}:
            return record
        config = await asyncio.to_thread(self.repo.get_turn_config, record.request_id)
        try:
            async with asyncio.timeout(10):
                client = await self.connect(
                    self.settings.temporal_address, namespace=self.settings.temporal_namespace
                )
                await client.start_workflow(
                    "ConversationWorkflow",
                    {"request_id": str(record.request_id), "config": config},
                    id=f"conversation:{record.request_id}",
                    task_queue=config["task_queue"],
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                )
        except WorkflowAlreadyStartedError:
            pass
        except (RPCError, OSError, TimeoutError, RuntimeError):
            raise InspectionStartError(
                "workflow_unavailable", "对话已保存，启动暂未确认；请用相同请求重试"
            ) from None
        return await asyncio.to_thread(self.repo.get_turn, record.request_id)
