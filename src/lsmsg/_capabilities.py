from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Platform = Literal["slack", "teams", "discord", "telegram", "github", "linear", "gchat"]


@dataclass(frozen=True, slots=True, kw_only=True)
class PlatformCapabilities:
    name: Platform
    ephemeral: bool = False
    threads: bool = False
    reactions: bool = False
    streaming: bool = False
    modals: bool = False
    typing_indicator: bool = False


SLACK_CAPABILITIES = PlatformCapabilities(
    name="slack",
    ephemeral=True,
    threads=True,
    reactions=True,
    streaming=True,
    modals=True,
    typing_indicator=True,
)

TEAMS_CAPABILITIES = PlatformCapabilities(
    name="teams",
    ephemeral=False,
    threads=True,
    reactions=False,
    streaming=False,
    modals=False,
    typing_indicator=True,
)

DISCORD_CAPABILITIES = PlatformCapabilities(
    name="discord",
    ephemeral=False,
    threads=True,
    reactions=True,
    streaming=False,
    modals=False,
    typing_indicator=True,
)

TELEGRAM_CAPABILITIES = PlatformCapabilities(
    name="telegram",
    ephemeral=False,
    threads=False,
    reactions=True,
    streaming=False,
    modals=False,
    typing_indicator=True,
)

GITHUB_CAPABILITIES = PlatformCapabilities(
    name="github",
    ephemeral=False,
    threads=True,
    reactions=False,
    streaming=False,
    modals=False,
    typing_indicator=False,
)

LINEAR_CAPABILITIES = PlatformCapabilities(
    name="linear",
    ephemeral=False,
    threads=True,
    reactions=True,
    streaming=False,
    modals=False,
    typing_indicator=False,
)

GCHAT_CAPABILITIES = PlatformCapabilities(
    name="gchat",
    ephemeral=True,
    threads=True,
    reactions=True,
    streaming=False,
    modals=False,
    typing_indicator=True,
)

CAPABILITIES: dict[Platform, PlatformCapabilities] = {
    "slack": SLACK_CAPABILITIES,
    "teams": TEAMS_CAPABILITIES,
    "discord": DISCORD_CAPABILITIES,
    "telegram": TELEGRAM_CAPABILITIES,
    "github": GITHUB_CAPABILITIES,
    "linear": LINEAR_CAPABILITIES,
    "gchat": GCHAT_CAPABILITIES,
}
