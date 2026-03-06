from ._bot import Bot
from ._capabilities import PlatformCapabilities
from ._errors import ConfigError, LsmsgError, PlatformNotSupported
from ._events import (
    BaseEvent,
    CommandEvent,
    MentionEvent,
    MessageEvent,
    RawEvent,
    ReactionEvent,
    UserInfo,
)
from ._platforms import Discord, GChat, GitHub, Linear, Slack, Teams, Telegram
from ._reply import SentMessage
from ._run import Run, RunChunk, RunResult
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._langgraph_backend import LangGraphRunBackend

__version__ = "0.1.0"


def __getattr__(name: str):
    if name == "LangGraphRunBackend":
        from ._langgraph_backend import LangGraphRunBackend

        return LangGraphRunBackend
    raise AttributeError(f"module 'lsmsg' has no attribute {name!r}")


__all__ = [
    "BaseEvent",
    "Bot",
    "CommandEvent",
    "ConfigError",
    "Discord",
    "GChat",
    "GitHub",
    "Linear",
    "LangGraphRunBackend",
    "LsmsgError",
    "MentionEvent",
    "MessageEvent",
    "PlatformCapabilities",
    "PlatformNotSupported",
    "RawEvent",
    "ReactionEvent",
    "Run",
    "RunChunk",
    "RunResult",
    "SentMessage",
    "Slack",
    "Teams",
    "Telegram",
    "UserInfo",
]
