from __future__ import annotations


from lsmsg import MentionEvent, MessageEvent, ReactionEvent
from lsmsg._teams import parse_teams_webhook


class TestTeamsEventParsing:
    def test_plain_message(self):
        payload = {
            "type": "message",
            "text": "hello everyone",
            "from": {"id": "user-1", "name": "Alice"},
            "conversation": {"id": "conv-1", "tenantId": "tenant-1"},
            "channelData": {"tenant": {"id": "tenant-1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
        }
        event = parse_teams_webhook(payload)
        assert isinstance(event, MessageEvent)
        assert event.text == "hello everyone"
        assert event.user.id == "user-1"
        assert event.user.name == "Alice"
        assert event.platform.name == "teams"

    def test_mention_with_at_tags(self):
        payload = {
            "type": "message",
            "text": "<at>MyBot</at> what time is it?",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "tenant-1"}, "team": {"id": "team-1"}},
            "entities": [{"type": "mention", "mentioned": {"id": "bot-1"}}],
            "id": "msg-1",
        }
        event = parse_teams_webhook(payload)
        assert isinstance(event, MentionEvent)
        assert event.text == "what time is it?"

    def test_mention_via_entities(self):
        payload = {
            "type": "message",
            "text": "hey bot",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {}, "team": {}},
            "entities": [{"type": "mention"}],
            "id": "msg-1",
        }
        event = parse_teams_webhook(payload)
        assert isinstance(event, MentionEvent)

    def test_reaction(self):
        payload = {
            "type": "messageReaction",
            "from": {"id": "user-1", "name": "Alice"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "reactionsAdded": [{"type": "like"}],
            "replyToId": "msg-original",
            "id": "react-1",
        }
        event = parse_teams_webhook(payload)
        assert isinstance(event, ReactionEvent)
        assert event.emoji == "like"

    def test_reaction_no_reactions_returns_none(self):
        payload = {
            "type": "messageReaction",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {}, "team": {}},
            "reactionsAdded": [],
            "id": "react-1",
        }
        event = parse_teams_webhook(payload)
        assert event is None

    def test_non_message_type_ignored(self):
        payload = {"type": "conversationUpdate", "id": "x"}
        event = parse_teams_webhook(payload)
        assert event is None

    def test_thread_id_from_reply_to_id(self):
        payload = {
            "type": "message",
            "text": "reply in thread",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {}, "team": {}},
            "replyToId": "parent-msg-1",
            "id": "msg-2",
        }
        event = parse_teams_webhook(payload)
        assert event.thread_id == "parent-msg-1"

    def test_mention_strips_multiple_at_tags(self):
        payload = {
            "type": "message",
            "text": "<at>Bot1</at> <at>Bot2</at> do something",
            "from": {"id": "user-1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {}, "team": {}},
            "entities": [{"type": "mention"}],
            "id": "msg-1",
        }
        event = parse_teams_webhook(payload)
        assert isinstance(event, MentionEvent)
        assert event.text == "do something"
