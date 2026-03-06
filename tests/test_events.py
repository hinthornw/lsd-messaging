from __future__ import annotations

import pytest

from lsmsg import (
    MentionEvent,
    PlatformNotSupported,
    UserInfo,
)
from lsmsg._capabilities import SLACK_CAPABILITIES, TEAMS_CAPABILITIES
from lsmsg._run import RunChunk

from .conftest import FakeMessageSender, FakeRunBackend


@pytest.fixture
def backend():
    return FakeRunBackend()


@pytest.fixture
def sender():
    return FakeMessageSender()


def _mention(backend, sender, **kw):
    defaults = dict(
        platform=SLACK_CAPABILITIES,
        workspace_id="T123",
        channel_id="C456",
        thread_id="ts-001",
        message_id="msg-001",
        user=UserInfo(id="U789"),
        text="hello",
        _run_backend=backend,
        _sender=sender,
    )
    defaults.update(kw)
    return MentionEvent(**defaults)


class TestInvoke:
    async def test_invoke_returns_run_result(self, backend, sender):
        event = _mention(backend, sender)
        result = await event.invoke("my-agent")
        assert result.status == "completed"
        assert result.text == "test response"
        assert len(backend.runs) == 1

    async def test_invoke_passes_custom_input(self, backend, sender):
        event = _mention(backend, sender)
        custom_input = {"messages": [{"role": "user", "content": "custom"}]}
        await event.invoke("my-agent", input=custom_input)
        run_data = list(backend.runs.values())[0]
        assert run_data["input"] == custom_input

    async def test_invoke_uses_event_text_as_default_input(self, backend, sender):
        event = _mention(backend, sender, text="what is 2+2?")
        await event.invoke("my-agent")
        run_data = list(backend.runs.values())[0]
        assert run_data["input"]["messages"][0]["content"] == "what is 2+2?"

    async def test_invoke_passes_config_and_metadata(self, backend, sender):
        event = _mention(backend, sender)
        await event.invoke(
            "my-agent",
            config={"configurable": {"model": "test"}},
            metadata={"user": "U789"},
        )
        run_data = list(backend.runs.values())[0]
        assert run_data["config"] == {"configurable": {"model": "test"}}
        assert run_data["metadata"] == {"user": "U789"}


class TestStream:
    async def test_stream_yields_chunks(self, backend, sender):
        backend.set_chunks(
            [
                RunChunk(event="values", text="Hello", text_delta="Hello"),
                RunChunk(event="values", text="Hello world", text_delta=" world"),
            ]
        )
        event = _mention(backend, sender)
        chunks = []
        async for chunk in event.stream("my-agent"):
            chunks.append(chunk)
        assert len(chunks) == 2
        assert chunks[0].text == "Hello"
        assert chunks[1].text_delta == " world"


class TestStart:
    async def test_start_returns_run_handle(self, backend, sender):
        event = _mention(backend, sender)
        run = await event.start("my-agent")
        assert run.id in backend.runs
        assert run.status == "running"

    async def test_run_wait(self, backend, sender):
        event = _mention(backend, sender)
        run = await event.start("my-agent")
        result = await run.wait()
        assert result.status == "completed"

    async def test_run_cancel(self, backend, sender):
        event = _mention(backend, sender)
        run = await event.start("my-agent")
        await run.cancel()
        assert run.id in backend.cancelled

    async def test_run_stream(self, backend, sender):
        backend.set_chunks(
            [
                RunChunk(event="values", text="hi", text_delta="hi"),
            ]
        )
        event = _mention(backend, sender)
        run = await event.start("my-agent")
        chunks = []
        async for chunk in run.stream():
            chunks.append(chunk)
        assert len(chunks) == 1


class TestReply:
    async def test_reply_sends_message(self, backend, sender):
        event = _mention(backend, sender)
        msg = await event.reply("hello")
        assert msg.id == "msg-1"
        assert sender.sent[0]["text"] == "hello"
        assert sender.sent[0]["platform"] == "slack"

    async def test_reply_returns_sent_message_with_update(self, backend, sender):
        event = _mention(backend, sender)
        msg = await event.reply("initial")
        await msg.update("updated")
        assert sender.updated[0]["message_id"] == "msg-1"
        assert sender.updated[0]["text"] == "updated"

    async def test_reply_returns_sent_message_with_delete(self, backend, sender):
        event = _mention(backend, sender)
        msg = await event.reply("to delete")
        await msg.delete()
        assert sender.deleted[0]["message_id"] == "msg-1"

    async def test_reply_with_blocks(self, backend, sender):
        event = _mention(backend, sender)
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
        await event.reply("hello", blocks=blocks)
        assert sender.sent[0]["blocks"] == blocks


class TestWhisper:
    async def test_whisper_on_slack_sends_ephemeral(self, backend, sender):
        event = _mention(backend, sender)
        await event.whisper("secret")
        assert sender.sent[0]["ephemeral"] is True
        assert sender.sent[0]["text"] == "secret"
        assert sender.sent[0]["user_id"] == "U789"

    async def test_whisper_on_teams_raises(self, backend, sender):
        event = MentionEvent(
            platform=TEAMS_CAPABILITIES,
            workspace_id="T123",
            channel_id="C456",
            thread_id="ts-001",
            message_id="msg-001",
            user=UserInfo(id="U789"),
            text="hello",
            _run_backend=backend,
            _sender=sender,
        )
        with pytest.raises(PlatformNotSupported, match="ephemeral.*teams"):
            await event.whisper("secret")

    async def test_whisper_fallback_reply(self, backend, sender):
        event = MentionEvent(
            platform=TEAMS_CAPABILITIES,
            workspace_id="T123",
            channel_id="C456",
            thread_id="ts-001",
            message_id="msg-001",
            user=UserInfo(id="U789"),
            text="hello",
            _run_backend=backend,
            _sender=sender,
        )
        await event.whisper("fallback msg", fallback="reply")
        assert sender.sent[0]["ephemeral"] is False
        assert sender.sent[0]["text"] == "fallback msg"


class TestThreadId:
    def test_deterministic_thread_id(self, backend, sender):
        e1 = _mention(backend, sender, thread_id="ts-001")
        e2 = _mention(backend, sender, thread_id="ts-001")
        e3 = _mention(backend, sender, thread_id="ts-002")
        assert e1.internal_thread_id == e2.internal_thread_id
        assert e1.internal_thread_id != e3.internal_thread_id

    def test_different_platforms_different_thread_ids(self, backend, sender):
        e1 = _mention(backend, sender, platform=SLACK_CAPABILITIES)
        e2 = MentionEvent(
            platform=TEAMS_CAPABILITIES,
            workspace_id="T123",
            channel_id="C456",
            thread_id="ts-001",
            message_id="msg-001",
            user=UserInfo(id="U789"),
            text="hello",
            _run_backend=backend,
            _sender=sender,
        )
        assert e1.internal_thread_id != e2.internal_thread_id


class TestRunResultText:
    def test_extracts_last_message_content(self):
        r = lsmsg_run_result(
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello back"},
            ]
        )
        assert r.text == "hello back"

    def test_empty_output(self):
        from lsmsg._run import RunResult

        r = RunResult(id="r1", status="completed", output={})
        assert r.text == ""

    def test_no_messages(self):
        from lsmsg._run import RunResult

        r = RunResult(id="r1", status="completed", output={"other": "data"})
        assert r.text == ""


def lsmsg_run_result(messages):
    from lsmsg._run import RunResult

    return RunResult(id="r1", status="completed", output={"messages": messages})
