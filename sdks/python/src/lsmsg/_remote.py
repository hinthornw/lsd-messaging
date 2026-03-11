"""Remote agent server abstractions."""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol, runtime_checkable

from lsmsg._types import RunChunk, RunResult

logger = logging.getLogger("lsmsg")


@runtime_checkable
class Remote(Protocol):
    """Protocol for remote agent servers that the bot can invoke."""

    async def invoke(
        self,
        agent: str,
        thread_id: str,
        input: Any,
        *,
        config: Any = None,
        metadata: Any = None,
    ) -> RunResult: ...

    async def stream(
        self,
        agent: str,
        thread_id: str,
        input: Any,
        *,
        config: Any = None,
        metadata: Any = None,
    ) -> list[RunChunk]: ...


class LangGraph:
    """A LangGraph server remote.

    When ``url`` is not provided, uses ASGI transport for in-process
    communication (the common local dev setup). When ``url`` is provided,
    connects over HTTP.
    """

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.url = url
        self.api_key = api_key
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from langgraph_sdk import get_client
        except ImportError:
            raise RuntimeError(
                "LangGraph remote requires langgraph-sdk. "
                "Install it with: pip install langgraph-sdk"
            )

        if self.url is not None:
            self._client = get_client(url=self.url, api_key=self.api_key)
        else:
            # ASGI transport mode (in-process)
            self._client = get_client()

        return self._client

    async def invoke(
        self,
        agent: str,
        thread_id: str,
        input: Any,
        *,
        config: Any = None,
        metadata: Any = None,
    ) -> RunResult:
        client = self._get_client()
        run = await client.runs.create(
            thread_id=thread_id,
            assistant_id=agent,
            input=input,
            config=config,
            metadata=metadata,
        )
        result = await client.runs.join(thread_id=thread_id, run_id=run["run_id"])
        return RunResult.from_dict(result)

    async def stream(
        self,
        agent: str,
        thread_id: str,
        input: Any,
        *,
        config: Any = None,
        metadata: Any = None,
    ) -> list[RunChunk]:
        client = self._get_client()
        chunks = []
        async for chunk in client.runs.stream(
            thread_id=thread_id,
            assistant_id=agent,
            input=input,
            config=config,
            metadata=metadata,
        ):
            chunks.append(
                RunChunk(
                    event=chunk.event or "",
                    text=getattr(chunk, "text", ""),
                    text_delta=getattr(chunk, "text_delta", ""),
                    data=chunk.data if hasattr(chunk, "data") else {},
                )
            )
        return chunks
