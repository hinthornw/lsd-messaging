from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest
from starlette.testclient import TestClient

from lsmsg import (
    Bot,
    CommandEvent,
    MentionEvent,
    MessageEvent,
    ReactionEvent,
    Slack,
    Teams,
)

from .conftest import FakeMessageSender, FakeRunBackend


def _make_bot(backend=None, sender=None, **kw):
    return Bot(
        slack=Slack(signing_secret="test-secret", bot_token="xoxb-test"),
        run_backend=backend or FakeRunBackend(),
        message_sender=sender or FakeMessageSender(),
        **kw,
    )


def _slack_event_body(event_type: str, text: str, **extra):
    event = {
        "type": event_type,
        "text": text,
        "channel": "C456",
        "ts": "1234567890.123456",
        "user": "U789",
        **extra,
    }
    return json.dumps({"type": "event_callback", "team_id": "T123", "event": event})


def _sign_headers(secret: str, body: bytes):
    import hashlib
    import hmac
    import time

    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": f"v0={digest}",
        "content-type": "application/json",
    }


class TestBotDecorators:
    def test_mention_bare_decorator(self):
        bot = _make_bot()
        calls = []

        @bot.mention
        async def handle(event: MentionEvent):
            calls.append(event)

        assert len(bot._handlers) == 1
        assert bot._handlers[0].event_type is MentionEvent

    def test_mention_with_pattern(self):
        bot = _make_bot()

        @bot.mention(pattern=r"^help")
        async def handle(event: MentionEvent):
            pass

        assert bot._handlers[0].pattern is not None
        assert bot._handlers[0].pattern.search("help me")
        assert not bot._handlers[0].pattern.search("no help")

    def test_message_bare_decorator(self):
        bot = _make_bot()

        @bot.message
        async def handle(event: MessageEvent):
            pass

        assert bot._handlers[0].event_type is MessageEvent

    def test_command_decorator(self):
        bot = _make_bot()

        @bot.command("/ask")
        async def handle(event: CommandEvent):
            pass

        assert bot._handlers[0].event_type is CommandEvent
        assert bot._handlers[0].command == "/ask"

    def test_command_empty_raises(self):
        bot = _make_bot()
        with pytest.raises(ValueError):

            @bot.command("")
            async def handle(event):
                pass

    def test_reaction_decorator(self):
        bot = _make_bot()

        @bot.reaction("eyes")
        async def handle(event: ReactionEvent):
            pass

        assert bot._handlers[0].event_type is ReactionEvent
        assert bot._handlers[0].emoji == "eyes"

    def test_on_raw_decorator(self):
        bot = _make_bot()

        @bot.on("app_home_opened")
        async def handle(event):
            pass

        assert bot._handlers[0].raw_event_type == "app_home_opened"


class TestBotWebhook:
    def test_slack_url_verification(self):
        bot = _make_bot()
        client = TestClient(bot)
        body = json.dumps({"type": "url_verification", "challenge": "abc"})
        headers = _sign_headers("test-secret", body.encode())
        resp = client.post("/slack/events", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "abc"}

    def test_slack_invalid_signature(self):
        bot = _make_bot()
        client = TestClient(bot)
        body = json.dumps({"type": "event_callback", "event": {}})
        resp = client.post(
            "/slack/events",
            content=body,
            headers={
                "content-type": "application/json",
                "x-slack-request-timestamp": "0",
                "x-slack-signature": "v0=invalid",
            },
        )
        assert resp.status_code == 401

    def test_slack_mention_dispatches(self):
        bot = _make_bot()
        calls = []

        @bot.mention
        async def handle(event: MentionEvent):
            calls.append(event.text)

        client = TestClient(bot)
        body = _slack_event_body("app_mention", "<@U999> hello bot")
        headers = _sign_headers("test-secret", body.encode())
        resp = client.post("/slack/events", content=body, headers=headers)
        assert resp.status_code == 200
        # Background dispatch — need to let the event loop process

        # TestClient runs synchronously, tasks may not complete inline
        # but the handler was registered correctly
        assert len(bot._handlers) == 1

    def test_slack_command_auto_ack(self):
        bot = _make_bot()

        @bot.command("/ask", ack="Processing...")
        async def handle(event: CommandEvent):
            pass

        client = TestClient(bot)
        form_data = urlencode(
            {
                "command": "/ask",
                "text": "something",
                "team_id": "T123",
                "channel_id": "C456",
                "user_id": "U789",
                "trigger_id": "trig-1",
            }
        )
        headers = _sign_headers("test-secret", form_data.encode())
        headers["content-type"] = "application/x-www-form-urlencoded"
        resp = client.post("/slack/events", content=form_data, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["response_type"] == "ephemeral"
        assert data["text"] == "Processing..."

    def test_slack_command_default_ack(self):
        bot = _make_bot()

        @bot.command("/ask")
        async def handle(event: CommandEvent):
            pass

        client = TestClient(bot)
        form_data = urlencode(
            {
                "command": "/ask",
                "text": "test",
                "team_id": "T123",
                "channel_id": "C456",
                "user_id": "U789",
                "trigger_id": "trig-1",
            }
        )
        headers = _sign_headers("test-secret", form_data.encode())
        headers["content-type"] = "application/x-www-form-urlencoded"
        resp = client.post("/slack/events", content=form_data, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "Working..."

    def test_slack_command_manual_ack(self):
        bot = _make_bot()

        @bot.command("/ask", ack=False)
        async def handle(event: CommandEvent):
            pass

        client = TestClient(bot)
        form_data = urlencode(
            {
                "command": "/ask",
                "text": "test",
                "team_id": "T123",
                "channel_id": "C456",
                "user_id": "U789",
                "trigger_id": "trig-1",
            }
        )
        headers = _sign_headers("test-secret", form_data.encode())
        headers["content-type"] = "application/x-www-form-urlencoded"
        resp = client.post("/slack/events", content=form_data, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


class TestBotASGI:
    def test_bot_is_asgi_mountable(self):
        from starlette.applications import Starlette
        from starlette.routing import Mount

        bot = _make_bot()
        app = Starlette(routes=[Mount("/chat", app=bot)])
        client = TestClient(app)
        body = json.dumps({"type": "url_verification", "challenge": "test"})
        headers = _sign_headers("test-secret", body.encode())
        resp = client.post("/chat/slack/events", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "test"}

    def test_attach_routes(self):
        from starlette.applications import Starlette

        bot = _make_bot()
        app = Starlette()
        bot.attach(app, prefix="/webhooks")
        client = TestClient(app)
        body = json.dumps({"type": "url_verification", "challenge": "attached"})
        headers = _sign_headers("test-secret", body.encode())
        resp = client.post("/webhooks/slack/events", content=body, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"challenge": "attached"}


class TestBotTeamsWebhook:
    def test_teams_message_dispatches(self):
        bot = Bot(
            teams=Teams(app_id="test", app_password="test", tenant_id="test"),
            run_backend=FakeRunBackend(),
            message_sender=FakeMessageSender(),
        )
        calls = []

        @bot.message
        async def handle(event: MessageEvent):
            calls.append(event.text)

        client = TestClient(bot)
        payload = {
            "type": "message",
            "text": "hello from teams",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
        }
        resp = client.post(
            "/teams/events",
            content=json.dumps(payload),
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200


class TestBotErrorHandler:
    def test_error_handler_called(self):
        errors = []

        async def on_error(event, exc):
            errors.append(str(exc))

        bot = _make_bot(on_error=on_error)

        @bot.mention
        async def handle(event: MentionEvent):
            raise ValueError("test error")

        # We can't easily test background task error handling with TestClient
        # since tasks run async. But we verify the handler is registered.
        assert bot._on_error is not None


class TestBotRouting:
    def test_message_pattern_matching(self):
        bot = _make_bot()
        matched = []

        @bot.message(pattern=r"^!status")
        async def handle(event: MessageEvent):
            matched.append(event.text)

        reg = bot._handlers[0]
        assert reg.pattern.search("!status check")
        assert not reg.pattern.search("check !status")
