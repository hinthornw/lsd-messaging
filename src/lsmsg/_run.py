from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Mapping, Protocol


@dataclass(frozen=True, slots=True, kw_only=True)
class RunResult:
    id: str
    status: str
    output: Mapping[str, Any]

    @property
    def text(self) -> str:
        messages = self.output.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                return str(last.get("content", ""))
        return ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RunChunk:
    event: str
    text: str
    text_delta: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class Run:
    id: str
    thread_id: str
    status: str = "pending"
    _backend: RunBackend

    async def wait(self, *, timeout: float = 300) -> RunResult:
        return await self._backend.wait_run(self.id, self.thread_id, timeout=timeout)

    async def stream(self) -> AsyncIterator[RunChunk]:
        async for chunk in self._backend.stream_run(self.id, self.thread_id):
            yield chunk

    async def cancel(self) -> None:
        await self._backend.cancel_run(self.id, self.thread_id)


class RunBackend(Protocol):
    async def create_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str: ...

    async def wait_run(
        self, run_id: str, thread_id: str, *, timeout: float = 300
    ) -> RunResult: ...

    def stream_run(self, run_id: str, thread_id: str) -> AsyncIterator[RunChunk]: ...

    def stream_new_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[RunChunk]: ...

    async def cancel_run(self, run_id: str, thread_id: str) -> None: ...
