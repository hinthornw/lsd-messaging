from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, cast

from langgraph_sdk import get_client

from ._run import RunChunk, RunResult


class LangGraphRunBackend:
    """RunBackend that uses the LangGraph SDK client.

    By default uses get_client() with no URL, which gives the in-process
    client when running inside the LangGraph server (the normal case).
    Pass url= for remote deployments.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._client = get_client(url=url, api_key=api_key)

    async def create_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        run = await self._client.runs.create(
            thread_id=thread_id,
            assistant_id=agent,
            input=cast(Any, input),
            config=cast(Any, config),
            metadata=cast(Any, metadata),
            if_not_exists="create",
        )
        return run["run_id"]

    async def wait_run(
        self,
        run_id: str,
        thread_id: str,
        *,
        timeout: float = 300,
    ) -> RunResult:
        data = await self._client.runs.join(thread_id, run_id)
        return RunResult(id=run_id, status="completed", output=data)

    async def stream_run(
        self,
        run_id: str,
        thread_id: str,
    ) -> AsyncIterator[RunChunk]:
        # Join an existing run — no real streaming support for pre-created runs
        data = await self._client.runs.join(thread_id, run_id)
        text = _extract_last_message_text(data)
        yield RunChunk(event="complete", text=text, text_delta=text, data=data)

    async def stream_new_run(
        self,
        *,
        agent: str,
        thread_id: str,
        input: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[RunChunk]:
        """Create a run and stream results via stream_mode='messages'."""
        accumulated = ""
        async for part in self._client.runs.stream(
            thread_id=thread_id,
            assistant_id=agent,
            input=cast(Any, input),
            config=cast(Any, config),
            metadata=cast(Any, metadata),
            stream_mode="messages",
            if_not_exists="create",
        ):
            text_delta = ""
            if part.event == "messages/partial":
                data = part.data
                if isinstance(data, list) and data:
                    last = data[-1] if isinstance(data[-1], dict) else {}
                    text_delta = str(last.get("content", ""))
                elif isinstance(data, dict):
                    text_delta = str(data.get("content", ""))
            accumulated += text_delta
            yield RunChunk(
                event=part.event,
                text=accumulated,
                text_delta=text_delta,
                data=part.data if isinstance(part.data, dict) else {},
            )

    async def cancel_run(self, run_id: str, thread_id: str) -> None:
        await self._client.runs.cancel(thread_id, run_id)

    async def close(self) -> None:
        await self._client.aclose()


def _extract_last_message_text(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    messages = data.get("messages", [])
    if messages and isinstance(messages[-1], dict):
        return str(messages[-1].get("content", ""))
    return ""
