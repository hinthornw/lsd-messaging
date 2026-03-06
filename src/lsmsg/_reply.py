from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class MessageSender(Protocol):
    async def send_message(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
        ephemeral: bool = False,
        user_id: str | None = None,
    ) -> str: ...

    async def update_message(
        self,
        *,
        platform: str,
        channel_id: str,
        message_id: str,
        text: str | None = None,
        blocks: list[Mapping[str, Any]] | None = None,
    ) -> None: ...

    async def delete_message(
        self,
        *,
        platform: str,
        channel_id: str,
        message_id: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class SentMessage:
    id: str
    platform: str
    channel_id: str
    _sender: MessageSender

    async def update(self, text: str) -> None:
        await self._sender.update_message(
            platform=self.platform,
            channel_id=self.channel_id,
            message_id=self.id,
            text=text,
        )

    async def delete(self) -> None:
        await self._sender.delete_message(
            platform=self.platform,
            channel_id=self.channel_id,
            message_id=self.id,
        )
