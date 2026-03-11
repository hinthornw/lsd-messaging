"""lsmsg - Async-first Python SDK for multi-platform messaging bots."""

from lsmsg._adapters import Adapter, Slack, Teams
from lsmsg._bot import Bot
from lsmsg._context import Context
from lsmsg._remote import LangGraph, Remote
from lsmsg._types import (
    Event,
    PlatformCapabilities,
    RunChunk,
    RunResult,
    SentMessage,
    UserInfo,
)

__all__ = [
    "Adapter",
    "Bot",
    "Context",
    "Event",
    "LangGraph",
    "PlatformCapabilities",
    "Remote",
    "RunChunk",
    "RunResult",
    "SentMessage",
    "Slack",
    "Teams",
    "UserInfo",
]
