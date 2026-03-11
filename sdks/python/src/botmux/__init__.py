"""botmux - Async-first Python SDK for multi-platform messaging bots."""

from botmux._adapters import Adapter, Slack, Teams
from botmux._bot import Bot
from botmux._context import Context
from botmux._remote import LangGraph, Remote
from botmux._types import (
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
