"""Tests for the Bot class."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from botmux._adapters import Slack, Teams
from botmux._bot import Bot
from botmux._context import Context


def make_slack_headers(
    body: bytes, *, content_type: str = "application/json", secret: str = "test-secret"
) -> dict[str, str]:
    timestamp = str(int(time.time()))
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return {
        "content-type": content_type,
        "x-slack-request-timestamp": timestamp,
        "x-slack-signature": f"v0={digest}",
    }


class TestBotCreation:
    def test_create_with_adapters(self):
        bot = Bot(
            adapters=[
                Slack(signing_secret="secret", bot_token="token"),
                Teams(app_id="app-id", app_password="password"),
            ],
        )
        assert len(bot._adapters) == 2

    def test_create_empty(self):
        bot = Bot()
        assert len(bot._adapters) == 0

    def test_create_with_no_remote(self):
        bot = Bot(adapters=[], remote=None)
        assert bot._remote is None

    def test_default_remote_is_langgraph(self):
        from botmux._remote import LangGraph

        bot = Bot()
        assert isinstance(bot._remote, LangGraph)

    def test_multiple_slack_adapters(self):
        bot = Bot(
            adapters=[
                Slack(signing_secret="s1", bot_token="t1", name="slack"),
                Slack(signing_secret="s2", bot_token="t2", name="slack-2"),
            ],
        )
        assert len(bot._adapters) == 2
        assert bot._adapters[0].name == "slack"
        assert bot._adapters[1].name == "slack-2"


class TestHandlerRegistration:
    def test_mention_decorator(self, bot):
        @bot.mention
        async def handler(ctx):
            pass

        assert len(bot._handlers) == 1
        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "mention"

    def test_message_decorator(self, bot):
        @bot.message
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "message"

    def test_message_with_pattern(self, bot):
        @bot.message(pattern=r"hello\s+world")
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["pattern"] == r"hello\s+world"

    def test_command_decorator(self, bot):
        @bot.command("/ask")
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "command"
        assert filt["command"] == "/ask"

    def test_reaction_decorator(self, bot):
        @bot.reaction("thumbsup")
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "reaction"
        assert filt["emoji"] == "thumbsup"

    def test_on_decorator(self, bot):
        @bot.on("app_home_opened")
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["raw_event_type"] == "app_home_opened"

    def test_platform_filter(self, bot):
        @bot.mention(platform="slack")
        async def handler(ctx):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["platform"] == "slack"

    def test_multiple_handlers(self, bot):
        @bot.mention
        async def h1(ctx):
            pass

        @bot.message
        async def h2(ctx):
            pass

        @bot.command("/test")
        async def h3(ctx):
            pass

        assert len(bot._handlers) == 3


class TestMatching:
    def test_match_mention(self, bot, make_event):
        @bot.mention
        async def handler(ctx):
            pass

        event = make_event(kind="mention")
        matched = bot._match_event(event)
        assert len(matched) == 1

    def test_no_match_wrong_kind(self, bot, make_event):
        @bot.mention
        async def handler(ctx):
            pass

        event = make_event(kind="message")
        matched = bot._match_event(event)
        assert len(matched) == 0

    def test_match_pattern(self, bot, make_event):
        @bot.message(pattern=r"hello")
        async def handler(ctx):
            pass

        event = make_event(kind="message", text="say hello world")
        matched = bot._match_event(event)
        assert len(matched) == 1

    def test_no_match_pattern(self, bot, make_event):
        @bot.message(pattern=r"goodbye")
        async def handler(ctx):
            pass

        event = make_event(kind="message", text="hello world")
        matched = bot._match_event(event)
        assert len(matched) == 0

    def test_match_command(self, bot, make_event):
        @bot.command("/ask")
        async def handler(ctx):
            pass

        event = make_event(kind="command", command="/ask")
        matched = bot._match_event(event)
        assert len(matched) == 1

    def test_no_match_wrong_command(self, bot, make_event):
        @bot.command("/ask")
        async def handler(ctx):
            pass

        event = make_event(kind="command", command="/echo")
        matched = bot._match_event(event)
        assert len(matched) == 0

    def test_match_reaction(self, bot, make_event):
        @bot.reaction("thumbsup")
        async def handler(ctx):
            pass

        event = make_event(kind="reaction", emoji="thumbsup")
        matched = bot._match_event(event)
        assert len(matched) == 1

    def test_no_match_wrong_emoji(self, bot, make_event):
        @bot.reaction("thumbsup")
        async def handler(ctx):
            pass

        event = make_event(kind="reaction", emoji="thumbsdown")
        matched = bot._match_event(event)
        assert len(matched) == 0

    def test_platform_filter_match(self, bot, make_event):
        @bot.mention(platform="slack")
        async def handler(ctx):
            pass

        event = make_event(kind="mention", platform_name="slack")
        matched = bot._match_event(event)
        assert len(matched) == 1

    def test_platform_filter_no_match(self, bot, make_event):
        @bot.mention(platform="teams")
        async def handler(ctx):
            pass

        event = make_event(kind="mention", platform_name="slack")
        matched = bot._match_event(event)
        assert len(matched) == 0

    def test_multiple_handlers_match(self, bot, make_event):
        @bot.mention
        async def h1(ctx):
            pass

        @bot.mention(pattern="hello")
        async def h2(ctx):
            pass

        event = make_event(kind="mention", text="hello world")
        matched = bot._match_event(event)
        assert len(matched) == 2

    def test_catch_all_handler(self, bot, make_event):
        """A handler with no filters matches everything."""

        @bot.on("app_mention")
        async def handler(ctx):
            pass

        event = make_event(kind="mention")
        matched = bot._match_event(event)
        assert len(matched) == 0


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_calls_handler(self, bot, make_event, mock_adapter):
        called_with = []

        @bot.mention
        async def handler(ctx):
            called_with.append(ctx)

        event = make_event(kind="mention")
        await bot.dispatch(event, mock_adapter)

        assert len(called_with) == 1
        assert isinstance(called_with[0], Context)
        assert called_with[0].event.kind == "mention"

    @pytest.mark.asyncio
    async def test_dispatch_no_match(self, bot, make_event, mock_adapter):
        called = []

        @bot.mention
        async def handler(ctx):
            called.append(True)

        event = make_event(kind="message")
        await bot.dispatch(event, mock_adapter)

        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_dispatch_multiple_handlers(self, bot, make_event, mock_adapter):
        results = []

        @bot.mention
        async def h1(ctx):
            results.append("h1")

        @bot.mention
        async def h2(ctx):
            results.append("h2")

        event = make_event(kind="mention")
        await bot.dispatch(event, mock_adapter)

        assert "h1" in results
        assert "h2" in results

    @pytest.mark.asyncio
    async def test_dispatch_handler_error_does_not_crash(
        self, bot, make_event, mock_adapter
    ):
        called = []

        @bot.mention
        async def bad_handler(ctx):
            raise ValueError("oops")

        @bot.mention
        async def good_handler(ctx):
            called.append(True)

        event = make_event(kind="mention")
        await bot.dispatch(event, mock_adapter)

        assert len(called) == 1


class TestContext:
    @pytest.mark.asyncio
    async def test_reply(self, bot, make_event, mock_adapter):
        event = make_event(kind="mention", text="hi")
        ctx = Context(event=event, adapter=mock_adapter, bot=bot)

        result = await ctx.reply("hello back")

        assert len(mock_adapter.sent_messages) == 1
        assert mock_adapter.sent_messages[0]["text"] == "hello back"
        assert mock_adapter.sent_messages[0]["channel_id"] == "C1"
        assert result.id == "mock-ts"

    @pytest.mark.asyncio
    async def test_whisper(self, bot, make_event, mock_adapter):
        event = make_event(kind="mention", text="hi", user_id="U1")
        ctx = Context(event=event, adapter=mock_adapter, bot=bot)

        await ctx.whisper("secret message")

        assert len(mock_adapter.sent_ephemeral) == 1
        assert mock_adapter.sent_ephemeral[0]["text"] == "secret message"
        assert mock_adapter.sent_ephemeral[0]["user_id"] == "U1"

    @pytest.mark.asyncio
    async def test_invoke_without_remote_raises(self, make_event, mock_adapter):
        bot = Bot(adapters=[], remote=None)
        event = make_event(kind="mention")
        ctx = Context(event=event, adapter=mock_adapter, bot=bot)

        with pytest.raises(RuntimeError, match="No remote configured"):
            await ctx.invoke("agent")


class TestASGI:
    @pytest.mark.asyncio
    async def test_slack_url_verification(self, bot):
        from httpx import ASGITransport, AsyncClient

        body = json.dumps(
            {"type": "url_verification", "challenge": "abc123"},
            separators=(",", ":"),
        ).encode()

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/slack/events",
                content=body,
                headers=make_slack_headers(body),
            )
            assert resp.status_code == 200
            assert resp.json()["challenge"] == "abc123"

    @pytest.mark.asyncio
    async def test_slack_event_dispatch(self, bot):
        called = []

        @bot.mention
        async def handler(ctx):
            called.append(ctx.event.text)

        from httpx import ASGITransport, AsyncClient

        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "app_mention",
                    "text": "<@UBOT> hello",
                    "channel": "C1",
                    "ts": "123.456",
                    "user": "U1",
                },
            },
            separators=(",", ":"),
        ).encode()

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/slack/events",
                content=body,
                headers=make_slack_headers(body),
            )
            assert resp.status_code == 200

        await bot.drain(timeout=5.0)
        assert "hello" in called

    @pytest.mark.asyncio
    async def test_slack_bot_message_ignored(self, bot):
        called = []

        @bot.message
        async def handler(ctx):
            called.append(True)

        from httpx import ASGITransport, AsyncClient

        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "bot_id": "B1",
                    "text": "bot message",
                    "channel": "C1",
                    "ts": "123.456",
                },
            },
            separators=(",", ":"),
        ).encode()

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/slack/events",
                content=body,
                headers=make_slack_headers(body),
            )
            assert resp.status_code == 200

        await bot.drain(timeout=5.0)
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_slack_slash_command(self, bot):
        called = []

        @bot.command("/echo")
        async def handler(ctx):
            called.append(ctx.event.text)

        from httpx import ASGITransport, AsyncClient

        body = (
            b"command=%2Fecho&text=hello+world&team_id=T1&channel_id=C1&user_id=U1"
            b"&trigger_id=trig1"
        )

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/slack/events",
                content=body,
                headers=make_slack_headers(
                    body, content_type="application/x-www-form-urlencoded"
                ),
            )
            assert resp.status_code == 200

        await bot.drain(timeout=5.0)
        assert "hello world" in called

    @pytest.mark.asyncio
    async def test_slack_missing_signature_headers_rejected(self, bot):
        from httpx import ASGITransport, AsyncClient

        body = json.dumps(
            {"type": "url_verification", "challenge": "abc123"},
            separators=(",", ":"),
        ).encode()

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/slack/events",
                content=body,
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_attach_mounts_single_prefix(self, bot):
        from starlette.applications import Starlette
        from httpx import ASGITransport, AsyncClient

        app = Starlette()
        bot.attach(app)
        body = json.dumps(
            {"type": "url_verification", "challenge": "abc123"},
            separators=(",", ":"),
        ).encode()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/botmux/slack/events",
                content=body,
                headers=make_slack_headers(body),
            )

        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123"

    @pytest.mark.asyncio
    async def test_teams_message(self):
        bot = Bot(
            adapters=[Teams(app_id="app", app_password="pass")],
            remote=None,
        )
        called = []

        @bot.message
        async def handler(ctx):
            called.append(ctx.event.text)

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/teams/events",
                json={
                    "type": "message",
                    "text": "hello teams",
                    "from": {"id": "U1", "name": "Alice"},
                    "conversation": {"id": "conv-1", "tenantId": "t1"},
                    "channelData": {
                        "tenant": {"id": "t1"},
                        "team": {"id": "team-1"},
                    },
                    "id": "msg-1",
                },
            )
            assert resp.status_code == 200

        await bot.drain(timeout=5.0)
        assert "hello teams" in called

    @pytest.mark.asyncio
    async def test_teams_invalid_json(self):
        bot = Bot(
            adapters=[Teams(app_id="app", app_password="pass")],
            remote=None,
        )

        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=bot), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/teams/events",
                content=b"not json",
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 400
