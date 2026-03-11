"""Context object passed to event handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from botmux._types import Event, RunChunk, RunResult, SentMessage

if TYPE_CHECKING:
    from botmux._adapters import Adapter
    from botmux._bot import Bot


class Context:
    """Passed to every handler. Provides the event data and action methods.

    Separates the pure data (``event``) from side-effects (``reply``,
    ``invoke``, ``stream``), making handlers easy to test.
    """

    __slots__ = ("event", "_adapter", "_bot")

    def __init__(self, event: Event, adapter: Adapter, bot: Bot) -> None:
        self.event = event
        self._adapter = adapter
        self._bot = bot

    async def reply(self, text: str) -> SentMessage:
        """Reply in the same channel/thread the event came from."""
        return await self._adapter.send_message(
            channel_id=self.event.channel_id,
            thread_id=self.event.thread_id,
            text=text,
        )

    async def whisper(self, text: str) -> SentMessage:
        """Send an ephemeral reply visible only to the triggering user."""
        return await self._adapter.send_ephemeral(
            channel_id=self.event.channel_id,
            thread_id=self.event.thread_id,
            user_id=self.event.user.id,
            text=text,
        )

    async def invoke(
        self,
        agent: str,
        *,
        input: Optional[Any] = None,
        config: Optional[Any] = None,
        metadata: Optional[Any] = None,
    ) -> RunResult:
        """Invoke an agent on the remote server and wait for the result."""
        remote = self._bot._remote
        if remote is None:
            raise RuntimeError("No remote configured on the Bot")

        if input is None:
            input = {"messages": [{"role": "user", "content": self.event.text}]}

        thread_id = self.event.internal_thread_id or ""
        return await remote.invoke(
            agent, thread_id, input, config=config, metadata=metadata
        )

    async def stream(
        self,
        agent: str,
        *,
        input: Optional[Any] = None,
        config: Optional[Any] = None,
        metadata: Optional[Any] = None,
    ) -> list[RunChunk]:
        """Stream an agent run on the remote server."""
        remote = self._bot._remote
        if remote is None:
            raise RuntimeError("No remote configured on the Bot")

        if input is None:
            input = {"messages": [{"role": "user", "content": self.event.text}]}

        thread_id = self.event.internal_thread_id or ""
        return await remote.stream(
            agent, thread_id, input, config=config, metadata=metadata
        )
