"""Shared test fixtures for lsmsg tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_native_module():
    """Mock the _lsmsg_core native module so tests work without building Rust."""
    mock_mod = MagicMock()

    # SlackParser
    mock_mod.SlackParser.verify_signature.return_value = True
    mock_mod.SlackParser.parse_webhook.return_value = {"type": "ignored"}
    mock_mod.SlackParser.strip_mentions.side_effect = lambda t: t

    # TeamsParser
    mock_mod.TeamsParser.parse_webhook.return_value = None
    mock_mod.TeamsParser.strip_mentions.side_effect = lambda t: t

    # PyHandlerRegistry
    mock_registry_instance = MagicMock()
    mock_registry_instance.register.return_value = 1
    mock_registry_instance.match_event.return_value = []
    mock_mod.PyHandlerRegistry.return_value = mock_registry_instance

    # PyLangGraphClient
    mock_lg_instance = MagicMock()
    mock_lg_instance.create_run.return_value = "run-123"
    mock_lg_instance.wait_run.return_value = {
        "id": "run-123",
        "status": "completed",
        "output": {"messages": [{"content": "hello"}]},
    }
    mock_mod.PyLangGraphClient.return_value = mock_lg_instance

    # deterministic_thread_id
    mock_mod.deterministic_thread_id.return_value = "test-thread-uuid"

    return mock_mod


@pytest.fixture
def bot():
    """Create a Bot instance that uses pure-Python fallback (no native ext)."""
    from lsmsg._bot import Bot

    return Bot(
        slack_signing_secret="test-secret",
        slack_bot_token="xoxb-test-token",
    )


@pytest.fixture
def make_event():
    """Factory for creating test Event objects."""
    from lsmsg._types import Event, PlatformCapabilities, UserInfo

    def _make(
        kind: str = "message",
        text: str = "hello",
        platform_name: str = "slack",
        command: str | None = None,
        emoji: str | None = None,
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
