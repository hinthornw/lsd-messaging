from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise KeyError(f"Missing required environment variable: {name}")
    return value.strip()


@dataclass(frozen=True, slots=True, kw_only=True)
class Slack:
    signing_secret: str = field(default_factory=lambda: _env("SLACK_SIGNING_SECRET"))
    bot_token: str = field(default_factory=lambda: _env("SLACK_BOT_TOKEN"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Teams:
    app_id: str = field(default_factory=lambda: _env("TEAMS_APP_ID"))
    app_password: str = field(default_factory=lambda: _env("TEAMS_APP_PASSWORD"))
    tenant_id: str = field(default_factory=lambda: _env("TEAMS_TENANT_ID"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Discord:
    public_key: str = field(default_factory=lambda: _env("DISCORD_PUBLIC_KEY"))
    bot_token: str = field(default_factory=lambda: _env("DISCORD_BOT_TOKEN"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Telegram:
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    webhook_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET"))


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHub:
    webhook_secret: str = field(default_factory=lambda: _env("GITHUB_WEBHOOK_SECRET"))
    token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))


@dataclass(frozen=True, slots=True, kw_only=True)
class Linear:
    webhook_secret: str = field(default_factory=lambda: _env("LINEAR_WEBHOOK_SECRET"))
    api_key: str = field(default_factory=lambda: _env("LINEAR_API_KEY"))


@dataclass(frozen=True, slots=True, kw_only=True)
class GChat:
    service_account_json: str = field(
        default_factory=lambda: _env("GCHAT_SERVICE_ACCOUNT_JSON")
    )
    project_id: str = field(default_factory=lambda: _env("GCHAT_PROJECT_ID"))
