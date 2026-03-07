"""Tests for the Bot class."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from lsmsg._bot import Bot


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
    def test_create_basic(self):
        bot = Bot(slack_signing_secret="secret", slack_bot_token="token")
        assert bot.slack_signing_secret == "secret"
        assert bot.slack_bot_token == "token"

    def test_create_with_teams(self):
        bot = Bot(teams_app_id="app-id", teams_app_password="password")
        assert bot.teams_app_id == "app-id"

    def test_create_with_langgraph(self):
        bot = Bot(langgraph_url="http://localhost:8000", langgraph_api_key="key")
        assert bot.langgraph_url == "http://localhost:8000"

    def test_create_empty(self):
        bot = Bot()
        assert bot.slack_signing_secret is None


class TestHandlerRegistration:
    def test_mention_decorator(self, bot):
        @bot.mention
        async def handler(event):
            pass

        assert len(bot._handlers) == 1
        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "mention"

    def test_message_decorator(self, bot):
        @bot.message
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "message"

    def test_message_with_pattern(self, bot):
        @bot.message(pattern=r"hello\s+world")
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["pattern"] == r"hello\s+world"

    def test_command_decorator(self, bot):
        @bot.command("/ask")
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "command"
        assert filt["command"] == "/ask"

    def test_reaction_decorator(self, bot):
        @bot.reaction("thumbsup")
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["event_kind"] == "reaction"
        assert filt["emoji"] == "thumbsup"

    def test_on_decorator(self, bot):
        @bot.on("app_home_opened")
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["raw_event_type"] == "app_home_opened"

    def test_platform_filter(self, bot):
        @bot.mention(platform="slack")
        async def handler(event):
            pass

        filt = list(bot._handler_filters.values())[0]
        assert filt["platform"] == "slack"

    def test_multiple_handlers(self, bot):
        @bot.mention
        async def h1(event):
            pass

        @bot.message
        async def h2(event):
            pass

        @bot.command("/test")
        async def h3(event):
            pass

        assert len(bot._handlers) == 3


class TestPythonMatching:
    def test_match_mention(self, bot, make_event):
        @bot.mention
        async def handler(event):
            pass

        event = make_event(kind="mention")
        matched = bot._match_event_python(event)
        assert len(matched) == 1

    def test_no_match_wrong_kind(self, bot, make_event):
        @bot.mention
        async def handler(event):
            pass

        event = make_event(kind="message")
        matched = bot._match_event_python(event)
        assert len(matched) == 0

    def test_match_pattern(self, bot, make_event):
        @bot.message(pattern=r"hello")
        async def handler(event):
            pass

        event = make_event(kind="message", text="say hello world")
        matched = bot._match_event_python(event)
        assert len(matched) == 1

    def test_no_match_pattern(self, bot, make_event):
        @bot.message(pattern=r"goodbye")
        async def handler(event):
            pass

        event = make_event(kind="message", text="hello world")
        matched = bot._match_event_python(event)
        assert len(matched) == 0

    def test_match_command(self, bot, make_event):
        @bot.command("/ask")
        async def handler(event):
            pass

        event = make_event(kind="command", command="/ask")
        matched = bot._match_event_python(event)
        assert len(matched) == 1

    def test_no_match_wrong_command(self, bot, make_event):
        @bot.command("/ask")
        async def handler(event):
            pass

        event = make_event(kind="command", command="/echo")
        matched = bot._match_event_python(event)
        assert len(matched) == 0

    def test_match_reaction(self, bot, make_event):
        @bot.reaction("thumbsup")
        async def handler(event):
            pass

        event = make_event(kind="reaction", emoji="thumbsup")
        matched = bot._match_event_python(event)
        assert len(matched) == 1

    def test_no_match_wrong_emoji(self, bot, make_event):
        @bot.reaction("thumbsup")
        async def handler(event):
            pass

        event = make_event(kind="reaction", emoji="thumbsdown")
        matched = bot._match_event_python(event)
        assert len(matched) == 0

    def test_platform_filter_match(self, bot, make_event):
        @bot.mention(platform="slack")
        async def handler(event):
            pass

        event = make_event(kind="mention", platform_name="slack")
        matched = bot._match_event_python(event)
        assert len(matched) == 1

    def test_platform_filter_no_match(self, bot, make_event):
        @bot.mention(platform="teams")
        async def handler(event):
            pass

        event = make_event(kind="mention", platform_name="slack")
        matched = bot._match_event_python(event)
        assert len(matched) == 0

    def test_multiple_handlers_match(self, bot, make_event):
        @bot.mention
        async def h1(event):
            pass

        @bot.mention(pattern="hello")
        async def h2(event):
            pass

        event = make_event(kind="mention", text="hello world")
        matched = bot._match_event_python(event)
        assert len(matched) == 2

    def test_catch_all_handler(self, bot, make_event):
        """A handler with no filters matches everything."""

        @bot.on("app_mention")
        async def handler(event):
            pass

        # Only matches if raw_event_type matches
        event = make_event(kind="mention")
        matched = bot._match_event_python(event)
        assert len(matched) == 0


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_calls_handler(self, bot, make_event):
        called_with = []

        @bot.mention
        async def handler(event):
            called_with.append(event)

        event = make_event(kind="mention")
        await bot.dispatch(event)

        # Allow background tasks to complete

        assert len(called_with) == 1
        assert called_with[0].kind == "mention"
        assert called_with[0]._bot is bot

    @pytest.mark.asyncio
    async def test_dispatch_no_match(self, bot, make_event):
        called = []

        @bot.mention
        async def handler(event):
            called.append(True)

        event = make_event(kind="message")
        await bot.dispatch(event)

        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_dispatch_multiple_handlers(self, bot, make_event):
        results = []

        @bot.mention
        async def h1(event):
            results.append("h1")

        @bot.mention
        async def h2(event):
            results.append("h2")

        event = make_event(kind="mention")
        await bot.dispatch(event)

        assert "h1" in results
        assert "h2" in results

    @pytest.mark.asyncio
    async def test_dispatch_handler_error_does_not_crash(self, bot, make_event):
        called = []

        @bot.mention
        async def bad_handler(event):
            raise ValueError("oops")

        @bot.mention
        async def good_handler(event):
            called.append(True)

        event = make_event(kind="mention")
        await bot.dispatch(event)

        # The good handler should still have been called
        assert len(called) == 1


class TestEventMethods:
    @pytest.mark.asyncio
    async def test_reply(self, bot, make_event):
        sent = []
        original_send = bot.send_message

        async def mock_send(**kwargs):
            sent.append(kwargs)
            return await original_send(**kwargs)

        bot.send_message = mock_send

        event = make_event(kind="mention", text="hi")
        # Bind bot
        from dataclasses import replace

        bound = replace(event, _bot=bot)
        await bound.reply("hello back")

        assert len(sent) == 1
        assert sent[0]["text"] == "hello back"
        assert sent[0]["channel_id"] == "C1"

    @pytest.mark.asyncio
    async def test_invoke_without_bot_raises(self, make_event):
        event = make_event(kind="mention")
        with pytest.raises(RuntimeError, match="not bound"):
            await event.invoke("agent")


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
        async def handler(event):
            called.append(event.text)

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
        async def handler(event):
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
        async def handler(event):
            called.append(event.text)

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
                "/lsmsg/slack/events",
                content=body,
                headers=make_slack_headers(body),
            )

        assert resp.status_code == 200
        assert resp.json()["challenge"] == "abc123"

    @pytest.mark.asyncio
    async def test_teams_message(self, bot):
        called = []

        @bot.message
        async def handler(event):
            called.append(event.text)

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
    async def test_teams_invalid_json(self, bot):
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
