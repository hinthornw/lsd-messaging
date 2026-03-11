"""Public data types for the botmux SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional


@dataclass(frozen=True, slots=True)
class PlatformCapabilities:
    name: str
    ephemeral: bool = False
    threads: bool = False
    reactions: bool = False
    streaming: bool = False
    modals: bool = False
    typing_indicator: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlatformCapabilities:
        return cls(
            name=d["name"],
            ephemeral=d.get("ephemeral", False),
            threads=d.get("threads", False),
            reactions=d.get("reactions", False),
            streaming=d.get("streaming", False),
            modals=d.get("modals", False),
            typing_indicator=d.get("typing_indicator", False),
        )


@dataclass(frozen=True, slots=True)
class UserInfo:
    id: str
    name: Optional[str] = None
    email: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserInfo:
        return cls(id=d["id"], name=d.get("name"), email=d.get("email"))


@dataclass(frozen=True, slots=True)
class RunResult:
    id: str
    status: str
    output: Any = None

    @property
    def text(self) -> str:
        if isinstance(self.output, dict):
            messages = self.output.get("messages")
            if isinstance(messages, list) and messages:
                last = messages[-1]
                if isinstance(last, dict):
                    return last.get("content", "")
        return ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunResult:
        return cls(id=d["id"], status=d["status"], output=d.get("output"))


@dataclass(frozen=True, slots=True)
class RunChunk:
    event: str = ""
    text: str = ""
    text_delta: str = ""
    data: Any = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunChunk:
        return cls(
            event=d.get("event", ""),
            text=d.get("text", ""),
            text_delta=d.get("text_delta", ""),
            data=d.get("data"),
        )


@dataclass(slots=True)
class SentMessage:
    id: str
    platform: str
    channel_id: str
    _update_fn: Optional[Callable[..., Coroutine[Any, Any, None]]] = field(
        default=None, repr=False, compare=False
    )
    _delete_fn: Optional[Callable[..., Coroutine[Any, Any, None]]] = field(
        default=None, repr=False, compare=False
    )

    async def update(self, text: str) -> None:
        if self._update_fn is not None:
            await self._update_fn(self, text)

    async def delete(self) -> None:
        if self._delete_fn is not None:
            await self._delete_fn(self)


@dataclass(frozen=True, slots=True)
class Event:
    """A normalized messaging event. Pure data — no methods with side effects."""

    kind: str
    platform: PlatformCapabilities
    workspace_id: str
    channel_id: str
    thread_id: str
    message_id: str
    user: UserInfo
    text: str
    command: Optional[str] = None
    emoji: Optional[str] = None
    raw_event_type: Optional[str] = None
    raw: Any = None
    internal_thread_id: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        platform_data = d.get("platform", {})
        if isinstance(platform_data, dict):
            platform = PlatformCapabilities.from_dict(platform_data)
        else:
            platform = platform_data

        user_data = d.get("user", {})
        if isinstance(user_data, dict):
            user = UserInfo.from_dict(user_data)
        else:
            user = user_data

        return cls(
            kind=d["kind"],
            platform=platform,
            workspace_id=d.get("workspace_id", ""),
            channel_id=d.get("channel_id", ""),
            thread_id=d.get("thread_id", ""),
            message_id=d.get("message_id", ""),
            user=user,
            text=d.get("text", ""),
            command=d.get("command"),
            emoji=d.get("emoji"),
            raw_event_type=d.get("raw_event_type"),
            raw=d.get("raw"),
            internal_thread_id=d.get("internal_thread_id"),
        )
