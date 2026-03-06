from __future__ import annotations


import pytest

from lsmsg import Slack, Teams, Discord


class TestSlackConfig:
    def test_explicit_values(self):
        s = Slack(signing_secret="whsec_test", bot_token="xoxb-test")
        assert s.signing_secret == "whsec_test"
        assert s.bot_token == "xoxb-test"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env-secret")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "env-token")
        s = Slack()
        assert s.signing_secret == "env-secret"
        assert s.bot_token == "env-token"

    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        with pytest.raises(KeyError, match="SLACK_SIGNING_SECRET"):
            Slack()

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "env")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "env")
        s = Slack(signing_secret="explicit", bot_token="explicit")
        assert s.signing_secret == "explicit"

    def test_frozen(self):
        s = Slack(signing_secret="x", bot_token="y")
        with pytest.raises(AttributeError):
            s.signing_secret = "z"  # type: ignore[misc]


class TestTeamsConfig:
    def test_explicit_values(self):
        t = Teams(app_id="id", app_password="pw", tenant_id="tid")
        assert t.app_id == "id"
        assert t.app_password == "pw"
        assert t.tenant_id == "tid"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("TEAMS_APP_ID", "env-id")
        monkeypatch.setenv("TEAMS_APP_PASSWORD", "env-pw")
        monkeypatch.setenv("TEAMS_TENANT_ID", "env-tid")
        t = Teams()
        assert t.app_id == "env-id"


class TestDiscordConfig:
    def test_explicit_values(self):
        d = Discord(public_key="pk", bot_token="bt")
        assert d.public_key == "pk"
        assert d.bot_token == "bt"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("DISCORD_PUBLIC_KEY", "env-pk")
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-bt")
        d = Discord()
        assert d.public_key == "env-pk"
