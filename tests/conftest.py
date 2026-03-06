from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Mapping

import pytest

from lsmsg._capabilities import SLACK_CAPABILITIES
from lsmsg._events import MentionEvent, UserInfo
from lsmsg._run import RunChunk, RunResult


class FakeRunBackend:
    """In-memory run backend for testing."""

    def __init__(self) -> None:
        self.runs: dict[str, dict[str, Any]] = {}
        self.cancelled: list[str] = []
        self._result: RunResult | None = None
        self._chunks: list[RunChunk] = []

    def set_result(self, result: RunResult) -> None:
        self._result = result

    def set_chunks(self, chunks: list[RunChunk]) -> None:
        self._chunks = chunks

    async def create_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        run_id = str(uuid.uuid4())
        self.runs[run_id] = {
            "agent": agent,
            "thread_id": thread_id,
            "input": input,
            "config": config,
            "metadata": metadata,
        }
        return run_id

    async def wait_run(
        self, run_id: str, thread_id: str, *, timeout: float = 300
    ) -> RunResult:
        if self._result is not None:
            return self._result
        return RunResult(
            id=run_id,
            status="completed",
            output={"messages": [{"role": "assistant", "content": "test response"}]},
        )

    async def stream_run(self, run_id: str, thread_id: str) -> AsyncIterator[RunChunk]:
        for chunk in self._chunks:
            yield chunk

    async def stream_new_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[RunChunk]:
        for chunk in self._chunks:
            yield chunk

    async def cancel_run(self, run_id: str, thread_id: str) -> None:
        self.cancelled.append(run_id)


class FakeMessageSender:
    """In-memory message sender for testing."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self._counter = 0

    async def send_message(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
        ephemeral: bool = False,
        user_id: str | None = None,
    ) -> str:
        self._counter += 1
        msg_id = f"msg-{self._counter}"
        self.sent.append(
            {
                "id": msg_id,
                "platform": platform,
                "channel_id": channel_id,
                "thread_id": thread_id,
                "text": text,
                "blocks": blocks,
                "ephemeral": ephemeral,
                "user_id": user_id,
            }
        )
        return msg_id

    async def update_message(
        self,
        *,
        platform: str,
        channel_id: str,
        message_id: str,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> None:
        self.updated.append(
            {
                "platform": platform,
                "channel_id": channel_id,
                "message_id": message_id,
                "text": text,
                "blocks": blocks,
            }
        )

    async def delete_message(
        self,
        *,
        platform: str,
        channel_id: str,
        message_id: str,
    ) -> None:
        self.deleted.append(
            {
                "platform": platform,
                "channel_id": channel_id,
                "message_id": message_id,
            }
        )


@pytest.fixture
def fake_run_backend():
    return FakeRunBackend()


@pytest.fixture
def fake_sender():
    return FakeMessageSender()


@pytest.fixture
def slack_mention_event(fake_run_backend, fake_sender):
    return MentionEvent(
        platform=SLACK_CAPABILITIES,
        workspace_id="T123",
        channel_id="C456",
        thread_id="1234567890.123456",
        message_id="msg-001",
        user=UserInfo(id="U789", name="testuser"),
        text="hello agent",
        _run_backend=fake_run_backend,
        _sender=fake_sender,
    )
