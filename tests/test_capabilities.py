from __future__ import annotations

from lsmsg._capabilities import (
    CAPABILITIES,
    DISCORD_CAPABILITIES,
    GCHAT_CAPABILITIES,
    SLACK_CAPABILITIES,
    TEAMS_CAPABILITIES,
    TELEGRAM_CAPABILITIES,
)


class TestCapabilities:
    def test_slack_has_all_features(self):
        c = SLACK_CAPABILITIES
        assert c.ephemeral is True
        assert c.threads is True
        assert c.reactions is True
        assert c.streaming is True
        assert c.modals is True
        assert c.typing_indicator is True

    def test_teams_no_ephemeral(self):
        assert TEAMS_CAPABILITIES.ephemeral is False

    def test_teams_no_streaming(self):
        assert TEAMS_CAPABILITIES.streaming is False

    def test_discord_no_ephemeral(self):
        assert DISCORD_CAPABILITIES.ephemeral is False

    def test_telegram_no_threads(self):
        assert TELEGRAM_CAPABILITIES.threads is False

    def test_gchat_has_ephemeral(self):
        assert GCHAT_CAPABILITIES.ephemeral is True

    def test_all_platforms_in_lookup(self):
        expected = {
            "slack",
            "teams",
            "discord",
            "telegram",
            "github",
            "linear",
            "gchat",
        }
        assert set(CAPABILITIES.keys()) == expected

    def test_capabilities_are_frozen(self):
        import pytest

        with pytest.raises(AttributeError):
            SLACK_CAPABILITIES.ephemeral = False  # type: ignore[misc]
