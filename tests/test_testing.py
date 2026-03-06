"""Tests for the lsmsg.testing module itself."""

from __future__ import annotations

import pytest

from lsmsg import (
    Bot,
    CommandEvent,
    MentionEvent,
    MessageEvent,
    ReactionEvent,
    Slack,
    Teams,
)
from lsmsg.testing import BotTestClient

from .conftest import FakeMessageSender, FakeRunBackend


def _make_bot(**kw):
    return Bot(
        slack=Slack(signing_secret="test-secret", bot_token="xoxb-test"),
        teams=Teams(app_id="test", app_password="test", tenant_id="test"),
        allow_unauthenticated_teams=True,
        run_backend=FakeRunBackend(),
        message_sender=FakeMessageSender(),
        **kw,
    )


class TestBotTestClientMention:
    def test_slack_mention(self):
        bot = _make_bot()
        calls = []

        @bot.mention
        async def handle(event: MentionEvent):
            calls.append(event.text)

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.mention("hello bot", platform="slack")
        assert result.status_code == 200

    def test_teams_mention(self):
        bot = _make_bot()

        @bot.mention
        async def handle(event: MentionEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.mention("hello bot", platform="teams")
        assert result.status_code == 200


class TestBotTestClientMessage:
    def test_slack_message(self):
        bot = _make_bot()

        @bot.message
        async def handle(event: MessageEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.message("hello", platform="slack")
        assert result.status_code == 200

    def test_teams_message(self):
        bot = _make_bot()

        @bot.message
        async def handle(event: MessageEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.message("hello", platform="teams")
        assert result.status_code == 200


class TestBotTestClientCommand:
    def test_command_with_custom_ack(self):
        bot = _make_bot()

        @bot.command("/ask", ack="Processing...")
        async def handle(event: CommandEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.command("/ask", "what is life")
        assert result.status_code == 200
        assert result.ack_text == "Processing..."
        assert result.ack_type == "ephemeral"

    def test_command_default_ack(self):
        bot = _make_bot()

        @bot.command("/ask")
        async def handle(event: CommandEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.command("/ask", "test")
        assert result.ack_text == "Working..."

    def test_command_manual_ack(self):
        bot = _make_bot()

        @bot.command("/ask", ack=False)
        async def handle(event: CommandEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.command("/ask", "test")
        assert result.response_json == {"ok": True}


class TestBotTestClientReaction:
    def test_slack_reaction(self):
        bot = _make_bot()

        @bot.reaction("eyes")
        async def handle(event: ReactionEvent):
            pass

        client = BotTestClient(bot, signing_secret="test-secret")
        result = client.reaction("eyes", platform="slack")
        assert result.status_code == 200


class TestBotTestClientUnsupported:
    def test_unsupported_mention_platform(self):
        bot = _make_bot()
        client = BotTestClient(bot)
        with pytest.raises(ValueError, match="Unsupported"):
            client.mention("hi", platform="discord")

    def test_unsupported_message_platform(self):
        bot = _make_bot()
        client = BotTestClient(bot)
        with pytest.raises(ValueError, match="Unsupported"):
            client.message("hi", platform="discord")

    def test_unsupported_reaction_platform(self):
        bot = _make_bot()
        client = BotTestClient(bot)
        with pytest.raises(ValueError, match="Unsupported"):
            client.reaction("eyes", platform="teams")
