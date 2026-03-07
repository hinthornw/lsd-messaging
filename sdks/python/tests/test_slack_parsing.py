"""Tests for Slack webhook parsing via the Python fallback parser."""

from __future__ import annotations

import json


from lsmsg._bot import _parse_slack_webhook_python


class TestUrlVerification:
    def test_challenge_response(self):
        body = json.dumps({"type": "url_verification", "challenge": "abc123"}).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "challenge"
        assert result["challenge"] == "abc123"

    def test_challenge_empty(self):
        body = json.dumps({"type": "url_verification"}).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "challenge"
        assert result["challenge"] == ""


class TestEventCallback:
    def test_mention_event(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "app_mention",
                    "text": "<@UBOT> hello world",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "event"
        ev = result["event"]
        assert ev["kind"] == "mention"
        assert ev["text"] == "hello world"
        assert ev["workspace_id"] == "T1"
        assert ev["channel_id"] == "C1"
        assert ev["user"]["id"] == "U1"

    def test_plain_message(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "text": "just a message",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "event"
        assert result["event"]["kind"] == "message"
        assert result["event"]["text"] == "just a message"

    def test_message_with_inline_mention_is_mention(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "text": "hey <@UBOT> help",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["event"]["kind"] == "mention"

    def test_reaction_added(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "reaction_added",
                    "reaction": "thumbsup",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "event"
        ev = result["event"]
        assert ev["kind"] == "reaction"
        assert ev["emoji"] == "thumbsup"

    def test_bot_message_ignored(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "bot_id": "B1",
                    "text": "bot says",
                    "channel": "C1",
                    "ts": "1234.5678",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "ignored"

    def test_bot_subtype_ignored(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "event": {
                    "type": "message",
                    "subtype": "bot_message",
                    "text": "bot says",
                    "channel": "C1",
                    "ts": "1234.5678",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "ignored"

    def test_uses_thread_ts_for_thread_id(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "text": "threaded",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "thread_ts": "1234.0000",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["event"]["thread_id"] == "1234.0000"

    def test_uses_client_msg_id_for_message_id(self):
        body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T1",
                "event": {
                    "type": "message",
                    "text": "msg",
                    "channel": "C1",
                    "ts": "1234.5678",
                    "client_msg_id": "uuid-123",
                    "user": "U1",
                },
            }
        ).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["event"]["message_id"] == "uuid-123"


class TestSlashCommands:
    def test_basic_command(self):
        body = b"command=%2Fecho&text=hello+world&team_id=T1&channel_id=C1&user_id=U1&user_name=alice&trigger_id=trig1"
        result = _parse_slack_webhook_python(body, "application/x-www-form-urlencoded")
        assert result["type"] == "event"
        ev = result["event"]
        assert ev["kind"] == "command"
        assert ev["command"] == "/echo"
        assert ev["text"] == "hello world"
        assert ev["user"]["id"] == "U1"
        assert ev["user"]["name"] == "alice"
        assert ev["workspace_id"] == "T1"
        assert ev["channel_id"] == "C1"

    def test_empty_command_ignored(self):
        body = b"command=&text=hello"
        result = _parse_slack_webhook_python(body, "application/x-www-form-urlencoded")
        assert result["type"] == "ignored"

    def test_no_command_field_ignored(self):
        body = b"text=hello"
        result = _parse_slack_webhook_python(body, "application/x-www-form-urlencoded")
        assert result["type"] == "ignored"


class TestInteractions:
    def test_block_actions(self):
        payload = json.dumps(
            {
                "type": "block_actions",
                "user": {"id": "U1", "username": "alice"},
                "channel": {"id": "C1"},
                "team": {"id": "T1"},
                "trigger_id": "trig1",
                "message": {
                    "text": "action message",
                    "ts": "1234.5678",
                },
            }
        )
        body = f"payload={payload}".encode()
        result = _parse_slack_webhook_python(body, "application/x-www-form-urlencoded")
        assert result["type"] == "event"
        assert result["event"]["kind"] == "message"

    def test_unknown_interaction_ignored(self):
        payload = json.dumps({"type": "unknown_type"})
        body = f"payload={payload}".encode()
        result = _parse_slack_webhook_python(body, "application/x-www-form-urlencoded")
        assert result["type"] == "ignored"


class TestEdgeCases:
    def test_invalid_json(self):
        result = _parse_slack_webhook_python(b"not json", "application/json")
        assert result["type"] == "ignored"

    def test_unknown_event_type(self):
        body = json.dumps({"type": "something_else"}).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "ignored"

    def test_event_callback_without_event_field(self):
        body = json.dumps({"type": "event_callback"}).encode()
        result = _parse_slack_webhook_python(body, "application/json")
        assert result["type"] == "ignored"
