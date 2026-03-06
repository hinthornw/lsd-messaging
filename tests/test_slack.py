from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode


from lsmsg import CommandEvent, MentionEvent, MessageEvent, ReactionEvent
from lsmsg._slack import parse_slack_webhook, verify_slack_signature


class TestSlackSignature:
    def _sign(self, secret: str, body: bytes, ts: str | None = None) -> dict[str, str]:
        ts = ts or str(int(time.time()))
        base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
        digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
        return {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": f"v0={digest}",
        }

    def test_valid_signature(self):
        body = b'{"type":"event_callback"}'
        headers = self._sign("mysecret", body)
        assert verify_slack_signature(
            signing_secret="mysecret", headers=headers, body=body
        )

    def test_invalid_signature(self):
        body = b'{"type":"event_callback"}'
        headers = self._sign("mysecret", body)
        assert not verify_slack_signature(
            signing_secret="wrongsecret", headers=headers, body=body
        )

    def test_missing_headers(self):
        assert not verify_slack_signature(
            signing_secret="mysecret", headers={}, body=b"test"
        )

    def test_expired_timestamp(self):
        body = b'{"type":"event_callback"}'
        old_ts = str(int(time.time()) - 400)
        headers = self._sign("mysecret", body, ts=old_ts)
        assert not verify_slack_signature(
            signing_secret="mysecret", headers=headers, body=body
        )


class TestSlackEventParsing:
    def test_url_verification(self):
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        result = parse_slack_webhook(body, {"content-type": "application/json"})
        assert result == {"challenge": "abc123"}

    def test_app_mention(self):
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "app_mention",
                "text": "<@U999> hello bot",
                "channel": "C456",
                "ts": "1234567890.123456",
                "user": "U789",
            },
        }
        body = json.dumps(payload).encode()
        event = parse_slack_webhook(body, {"content-type": "application/json"})
        assert isinstance(event, MentionEvent)
        assert event.text == "hello bot"
        assert event.workspace_id == "T123"
        assert event.channel_id == "C456"
        assert event.user.id == "U789"
        assert event.platform.name == "slack"

    def test_regular_message(self):
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "text": "just chatting",
                "channel": "C456",
                "ts": "1234567890.123456",
                "user": "U789",
            },
        }
        body = json.dumps(payload).encode()
        event = parse_slack_webhook(body, {"content-type": "application/json"})
        assert isinstance(event, MessageEvent)
        assert event.text == "just chatting"

    def test_bot_messages_ignored(self):
        payload = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "text": "bot talking",
                "bot_id": "B123",
                "channel": "C456",
                "ts": "123",
            },
        }
        body = json.dumps(payload).encode()
        event = parse_slack_webhook(body, {"content-type": "application/json"})
        assert event is None

    def test_reaction_added(self):
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "reaction_added",
                "reaction": "eyes",
                "user": "U789",
                "channel": "C456",
                "ts": "123",
            },
        }
        body = json.dumps(payload).encode()
        event = parse_slack_webhook(body, {"content-type": "application/json"})
        assert isinstance(event, ReactionEvent)
        assert event.emoji == "eyes"

    def test_threaded_message(self):
        payload = {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "app_mention",
                "text": "<@U999> reply here",
                "channel": "C456",
                "ts": "1234567890.999999",
                "thread_ts": "1234567890.000001",
                "user": "U789",
            },
        }
        body = json.dumps(payload).encode()
        event = parse_slack_webhook(body, {"content-type": "application/json"})
        assert isinstance(event, MentionEvent)
        assert event.thread_id == "1234567890.000001"

    def test_invalid_json(self):
        result = parse_slack_webhook(b"not json", {"content-type": "application/json"})
        assert result is None

    def test_non_event_callback(self):
        body = json.dumps({"type": "something_else"}).encode()
        result = parse_slack_webhook(body, {"content-type": "application/json"})
        assert result is None


class TestSlackSlashCommand:
    def test_slash_command(self):
        form_data = urlencode(
            {
                "command": "/ask",
                "text": "what is the meaning of life",
                "team_id": "T123",
                "channel_id": "C456",
                "user_id": "U789",
                "user_name": "testuser",
                "trigger_id": "trigger-001",
            }
        ).encode()
        event = parse_slack_webhook(
            form_data,
            {"content-type": "application/x-www-form-urlencoded"},
        )
        assert isinstance(event, CommandEvent)
        assert event.command == "/ask"
        assert event.text == "what is the meaning of life"
        assert event.user.id == "U789"
        assert event.user.name == "testuser"

    def test_empty_command_ignored(self):
        form_data = urlencode({"command": "", "text": "test"}).encode()
        event = parse_slack_webhook(
            form_data,
            {"content-type": "application/x-www-form-urlencoded"},
        )
        assert event is None


class TestSlackInteraction:
    def test_block_action(self):
        interaction = {
            "type": "block_actions",
            "user": {"id": "U789", "username": "testuser"},
            "channel": {"id": "C456"},
            "team": {"id": "T123"},
            "message": {"text": "click me", "ts": "123"},
            "trigger_id": "trig-001",
        }
        form_data = urlencode({"payload": json.dumps(interaction)}).encode()
        event = parse_slack_webhook(
            form_data,
            {"content-type": "application/x-www-form-urlencoded"},
        )
        assert isinstance(event, MessageEvent)
        assert event.text == "click me"
        assert event.user.id == "U789"
