"""End-to-end integration tests.

These tests require a running LangGraph server:
    uv run langgraph dev --port 2024

They exercise the full flow:
    webhook payload → Bot dispatch → LangGraphRunBackend → real agent → reply

Run with:
    pytest tests/integration/ -v

Skip in CI without a server:
    pytest tests/integration/ -v --langgraph-url http://localhost:2024
"""

from __future__ import annotations


import httpx
import pytest

from lsmsg import Bot, LangGraphRunBackend, MentionEvent, Slack
from lsmsg._capabilities import SLACK_CAPABILITIES
from lsmsg._events import UserInfo
from lsmsg._run import RunChunk

from ..conftest import FakeMessageSender

LANGGRAPH_URL = "http://localhost:2024"


def _server_available() -> bool:
    try:
        resp = httpx.get(f"{LANGGRAPH_URL}/ok", timeout=2)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


pytestmark = pytest.mark.skipif(
    not _server_available(),
    reason=f"LangGraph server not running at {LANGGRAPH_URL}",
)


@pytest.fixture
def backend():
    return LangGraphRunBackend(base_url=LANGGRAPH_URL)


@pytest.fixture
def sender():
    return FakeMessageSender()


def _make_event(backend, sender, text="hello"):
    return MentionEvent(
        platform=SLACK_CAPABILITIES,
        workspace_id="T-test",
        channel_id="C-test",
        thread_id="ts-integration-001",
        message_id="msg-int-001",
        user=UserInfo(id="U-test", name="integration-tester"),
        text=text,
        _run_backend=backend,
        _sender=sender,
    )


class TestInvokeE2E:
    async def test_invoke_creates_run_and_gets_result(self, backend, sender):
        """Full round-trip: create run → wait → get result."""
        event = _make_event(backend, sender, text="Say exactly: INTEGRATION_OK")
        result = await event.invoke("agent")
        assert result.status == "completed"
        assert result.id  # has a real run ID
        assert isinstance(result.output, dict)

    async def test_invoke_with_custom_input(self, backend, sender):
        event = _make_event(backend, sender)
        result = await event.invoke(
            "agent",
            input={
                "messages": [
                    {"role": "user", "content": "Say exactly: CUSTOM_INPUT_OK"}
                ]
            },
        )
        assert result.status == "completed"
        assert result.id


class TestStartE2E:
    async def test_start_wait(self, backend, sender):
        """Start a run, get handle, wait for completion."""
        event = _make_event(backend, sender, text="Say hi")
        run = await event.start("agent")
        assert run.id
        assert run.thread_id
        result = await run.wait(timeout=30)
        assert result.status == "completed"

    async def test_start_cancel(self, backend, sender):
        """Start a run and cancel it."""
        event = _make_event(
            backend, sender, text="Write a very long essay about nothing"
        )
        run = await event.start("agent")
        # Cancel immediately
        try:
            await run.cancel()
        except httpx.HTTPStatusError:
            # Run may have already completed — that's ok
            pass


class TestStreamE2E:
    async def test_stream_yields_chunks(self, backend, sender):
        """Stream a run and collect chunks."""
        event = _make_event(backend, sender, text="Count from 1 to 3")
        chunks: list[RunChunk] = []
        async for chunk in event.stream("agent"):
            chunks.append(chunk)
        assert len(chunks) > 0
        # Last chunk should have accumulated text
        assert chunks[-1].text


class TestFullBotE2E:
    """Full bot integration: webhook → dispatch → langgraph → reply."""

    async def test_mention_triggers_invoke_and_reply(self, backend, sender):
        bot = Bot(
            slack=Slack(signing_secret="test-secret", bot_token="xoxb-test"),
            run_backend=backend,
            message_sender=sender,
        )

        @bot.mention
        async def handle(event: MentionEvent):
            result = await event.invoke("agent")
            await event.reply(result.text)

        event = _make_event(backend, sender, text="Say exactly: BOT_E2E_OK")
        # Dispatch directly (bypassing HTTP layer)
        await bot._dispatch(event)

        assert sender.sent, "Bot should have sent a reply"
        assert sender.sent[0]["text"]  # reply has content

    async def test_mention_stream_and_update(self, backend, sender):
        bot = Bot(
            slack=Slack(signing_secret="test-secret", bot_token="xoxb-test"),
            run_backend=backend,
            message_sender=sender,
        )

        @bot.mention
        async def handle(event: MentionEvent):
            msg = await event.reply("Thinking...")
            async for chunk in event.stream("agent"):
                await msg.update(chunk.text)

        event = _make_event(backend, sender, text="Count to 3")
        await bot._dispatch(event)

        assert sender.sent, "Should have initial reply"
        assert sender.sent[0]["text"] == "Thinking..."
        # Should have updates from streaming
        if sender.updated:
            assert sender.updated[-1]["text"]  # last update has content
