from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, TYPE_CHECKING

from ._capabilities import PlatformCapabilities
from ._errors import PlatformNotSupported
from ._reply import SentMessage
from ._run import Run, RunChunk, RunResult

if TYPE_CHECKING:
    from ._reply import MessageSender
    from ._run import RunBackend


def _deterministic_thread_id(
    platform: str, workspace_id: str, channel_id: str, thread_id: str
) -> str:
    namespace = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    key = f"{platform}:{workspace_id}:{channel_id}:{thread_id}"
    return str(uuid.uuid5(namespace, key))


@dataclass(frozen=True, slots=True, kw_only=True)
class UserInfo:
    id: str
    name: str | None = None
    email: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BaseEvent:
    platform: PlatformCapabilities
    workspace_id: str
    channel_id: str
    thread_id: str
    message_id: str
    user: UserInfo
    text: str
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def internal_thread_id(self) -> str:
        return _deterministic_thread_id(
            self.platform.name, self.workspace_id, self.channel_id, self.thread_id
        )

    # -- injected by Bot at dispatch time --

    _run_backend: RunBackend = field(repr=False, compare=False, default=None)  # type: ignore[assignment]
    _sender: MessageSender = field(repr=False, compare=False, default=None)  # type: ignore[assignment]

    # -- Run shortcuts (layer 1) --

    async def invoke(
        self,
        agent: str,
        *,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        timeout: float = 300,
    ) -> RunResult:
        run = await self.start(agent, input=input, config=config, metadata=metadata)
        return await run.wait(timeout=timeout)

    async def stream(
        self,
        agent: str,
        *,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[RunChunk]:
        tid = self.internal_thread_id
        resolved_input = (
            input
            if input is not None
            else {"messages": [{"role": "user", "content": self.text}]}
        )
        async for chunk in self._run_backend.stream_new_run(
            agent=agent,
            thread_id=tid,
            input=resolved_input,
            config=config,
            metadata=metadata,
        ):
            yield chunk

    # -- Run lifecycle (layer 2) --

    async def start(
        self,
        agent: str,
        *,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Run:
        tid = self.internal_thread_id
        resolved_input = (
            input
            if input is not None
            else {"messages": [{"role": "user", "content": self.text}]}
        )
        run_id = await self._run_backend.create_run(
            agent=agent,
            thread_id=tid,
            input=resolved_input,
            config=config,
            metadata=metadata,
        )
        return Run(
            id=run_id, thread_id=tid, status="running", _backend=self._run_backend
        )

    # -- Reply --

    async def reply(
        self,
        text: str,
        *,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> SentMessage:
        msg_id = await self._sender.send_message(
            platform=self.platform.name,
            channel_id=self.channel_id,
            thread_id=self.thread_id,
            text=text,
            blocks=blocks,
        )
        return SentMessage(
            id=msg_id,
            platform=self.platform.name,
            channel_id=self.channel_id,
            _sender=self._sender,
        )

    async def whisper(
        self,
        text: str,
        *,
        fallback: str | None = None,
    ) -> None:
        if not self.platform.ephemeral:
            if fallback == "reply":
                await self.reply(text)
                return
            raise PlatformNotSupported("ephemeral messages", self.platform.name)
        await self._sender.send_message(
            platform=self.platform.name,
            channel_id=self.channel_id,
            thread_id=self.thread_id,
            text=text,
            ephemeral=True,
            user_id=self.user.id,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MentionEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageEvent(BaseEvent):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandEvent(BaseEvent):
    command: str = ""

    # ack is handled by the Bot at dispatch time.
    # The event carries the ack text set by the decorator.
    _ack_text: str | None = field(default=None, repr=False, compare=False)
    _ack_sent: bool = field(default=False, repr=False, compare=False)

    async def ack(self, text: str | None = None) -> None:
        # Manual ack for ack=False commands. Implemented by Bot.
        pass


@dataclass(frozen=True, slots=True, kw_only=True)
class ReactionEvent(BaseEvent):
    emoji: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RawEvent(BaseEvent):
    event_type: str = ""
