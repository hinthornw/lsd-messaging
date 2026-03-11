"""Shared test fixtures for lsmsg tests."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from lsmsg._adapters import Slack
from lsmsg._types import Event, PlatformCapabilities, SentMessage, UserInfo


class MockAdapter:
    """A test adapter that records sent messages instead of making API calls."""

    def __init__(self, name: str = "slack") -> None:
        self.name = name
        self.sent_messages: list[dict[str, Any]] = []
        self.sent_ephemeral: list[dict[str, Any]] = []

    def routes(self, dispatch):
        return []

    async def send_message(
        self, *, channel_id: str, thread_id: str, text: str
    ) -> SentMessage:
        msg = {"channel_id": channel_id, "thread_id": thread_id, "text": text}
        self.sent_messages.append(msg)
        return SentMessage(id="mock-ts", platform=self.name, channel_id=channel_id)

    async def send_ephemeral(
        self, *, channel_id: str, thread_id: str, user_id: str, text: str
    ) -> SentMessage:
        msg = {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "text": text,
        }
        self.sent_ephemeral.append(msg)
        return SentMessage(id="mock-ts", platform=self.name, channel_id=channel_id)


@pytest.fixture
def mock_adapter():
    return MockAdapter()


@pytest.fixture
def bot():
    """Create a Bot instance with a Slack adapter (pure-Python fallback)."""
    from lsmsg._bot import Bot

    return Bot(
        adapters=[
            Slack(signing_secret="test-secret", bot_token="xoxb-test-token"),
        ],
        remote=None,
    )


@pytest.fixture
def make_event():
    """Factory for creating test Event objects."""

    def _make(
        kind: str = "message",
        text: str = "hello",
        platform_name: str = "slack",
        command: Optional[str] = None,
        emoji: Optional[str] = None,
        user_id: str = "U1",
        channel_id: str = "C1",
        workspace_id: str = "T1",
        thread_id: str = "t1",
    ) -> Event:
        caps = PlatformCapabilities(
            name=platform_name,
            ephemeral=platform_name == "slack",
            threads=True,
            reactions=platform_name == "slack",
            streaming=platform_name == "slack",
            modals=platform_name == "slack",
            typing_indicator=True,
        )
        return Event(
            kind=kind,
            platform=caps,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_id=thread_id,
            message_id="m1",
            user=UserInfo(id=user_id),
            text=text,
            command=command,
            emoji=emoji,
            internal_thread_id="test-thread-id",
        )

    return _make
