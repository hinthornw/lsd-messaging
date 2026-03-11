"""Tests for Teams webhook parsing via the Python fallback parser."""

from __future__ import annotations


from botmux._adapters import _parse_teams_webhook as _raw_parse


def _parse_teams_webhook_python(payload):
    """Wrapper that unwraps the dispatch envelope for backward-compat tests."""
    result = _raw_parse(payload)
    if result is None:
        return None
    if "event" in result:
        return result["event"]
    return result


class TestTeamsMessages:
    def test_plain_message(self):
        payload = {
            "type": "message",
            "text": "hello",
            "from": {"id": "U1", "name": "Alice"},
            "conversation": {"id": "conv-1", "tenantId": "tenant-1"},
            "channelData": {
                "tenant": {"id": "tenant-1"},
                "team": {"id": "team-1"},
            },
            "id": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is not None
        assert result["kind"] == "message"
        assert result["text"] == "hello"
        assert result["user"]["id"] == "U1"
        assert result["user"]["name"] == "Alice"
        assert result["workspace_id"] == "tenant-1"
        assert result["channel_id"] == "team-1"

    def test_message_with_reply_to_id(self):
        payload = {
            "type": "message",
            "text": "reply",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-2",
            "replyToId": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result["thread_id"] == "msg-1"


class TestTeamsMentions:
    def test_at_mention(self):
        payload = {
            "type": "message",
            "text": "<at>Bot</at> help me",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
            "entities": [{"type": "mention", "mentioned": {"id": "bot-1"}}],
        }
        result = _parse_teams_webhook_python(payload)
        assert result is not None
        assert result["kind"] == "mention"
        assert result["text"] == "help me"

    def test_mention_without_entities_but_at_tag(self):
        payload = {
            "type": "message",
            "text": "<at>Bot</at> do something",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result["kind"] == "mention"


class TestTeamsReactions:
    def test_reaction_added(self):
        payload = {
            "type": "messageReaction",
            "reactionsAdded": [{"type": "like"}],
            "from": {"id": "U1", "name": "Bob"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "replyToId": "msg-orig",
            "id": "reaction-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is not None
        assert result["kind"] == "reaction"
        assert result["emoji"] == "like"
        assert result["thread_id"] == "msg-orig"

    def test_empty_reactions_returns_none(self):
        payload = {
            "type": "messageReaction",
            "reactionsAdded": [],
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "reaction-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is None

    def test_no_reactions_field_returns_none(self):
        payload = {
            "type": "messageReaction",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "reaction-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is None


class TestTeamsUnknownTypes:
    def test_typing_ignored(self):
        result = _parse_teams_webhook_python({"type": "typing"})
        assert result is None

    def test_empty_type_ignored(self):
        result = _parse_teams_webhook_python({"type": ""})
        assert result is None

    def test_no_type_ignored(self):
        result = _parse_teams_webhook_python({})
        assert result is None


class TestTeamsEdgeCases:
    def test_missing_from(self):
        payload = {
            "type": "message",
            "text": "hello",
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is not None
        assert result["user"]["id"] == "unknown"

    def test_missing_channel_data(self):
        payload = {
            "type": "message",
            "text": "hello",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "id": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result is not None
        assert result["workspace_id"] == "unknown"

    def test_platform_is_teams(self):
        payload = {
            "type": "message",
            "text": "hello",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
        }
        result = _parse_teams_webhook_python(payload)
        assert result["platform"]["name"] == "teams"
