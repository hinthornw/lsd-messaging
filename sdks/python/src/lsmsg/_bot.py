"""Bot class: the main entry point for building messaging bots with lsmsg."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Coroutine, Optional, Sequence

from starlette.applications import Starlette
from starlette.routing import Route

from lsmsg._adapters import Adapter
from lsmsg._context import Context
from lsmsg._remote import LangGraph, Remote
from lsmsg._types import Event

logger = logging.getLogger("lsmsg")

HandlerFunc = Callable[[Context], Coroutine[Any, Any, Any]]

_UNSET = object()


class Bot:
    """An async-first messaging bot that acts as an ASGI application.

    Example::

        bot = Bot(adapters=[
            Slack(signing_secret="...", bot_token="..."),
            Teams(app_id="...", app_password="..."),
        ])

        @bot.mention
        async def on_mention(ctx):
            result = await ctx.invoke("agent")
            await ctx.reply(result.text)
    """

    def __init__(
        self,
        *,
        adapters: Sequence[Adapter] = (),
        remote: Optional[Remote] = _UNSET,  # type: ignore[assignment]
    ) -> None:
        self._adapters = list(adapters)
        # Default remote: LangGraph with ASGI transport.
        # Pass remote=None explicitly to disable.
        if remote is _UNSET:
            self._remote: Optional[Remote] = LangGraph()
        else:
            self._remote = remote

        # Handler registry (pure-Python matching)
        self._handlers: dict[int, HandlerFunc] = {}
        self._handler_filters: dict[int, dict[str, Any]] = {}
        self._next_id = 1

        # Track background dispatch tasks
        self._pending_tasks: set[asyncio.Task[None]] = set()

        # ASGI app (built lazily)
        self._app: Optional[Starlette] = None

    # ------------------------------------------------------------------
    # Decorators / handler registration
    # ------------------------------------------------------------------

    def mention(
        self,
        func: Optional[HandlerFunc] = None,
        *,
        pattern: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Any:
        """Register a handler for mention events."""

        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self._register_handler(
                fn, event_kind="mention", pattern=pattern, platform=platform
            )
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    def message(
        self,
        func: Optional[HandlerFunc] = None,
        *,
        pattern: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Any:
        """Register a handler for message events."""

        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self._register_handler(
                fn, event_kind="message", pattern=pattern, platform=platform
            )
            return fn

        if func is not None:
            return decorator(func)
        return decorator

    def command(
        self,
        name: str,
        *,
        pattern: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Callable[[HandlerFunc], HandlerFunc]:
        """Register a handler for a slash command."""

        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self._register_handler(
                fn,
                event_kind="command",
                command=name,
                pattern=pattern,
                platform=platform,
            )
            return fn

        return decorator

    def reaction(
        self,
        emoji: str,
        *,
        platform: Optional[str] = None,
    ) -> Callable[[HandlerFunc], HandlerFunc]:
        """Register a handler for a reaction event with a specific emoji."""

        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self._register_handler(
                fn,
                event_kind="reaction",
                emoji=emoji,
                platform=platform,
            )
            return fn

        return decorator

    def on(
        self,
        event_type: str,
        *,
        pattern: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> Callable[[HandlerFunc], HandlerFunc]:
        """Register a handler for a raw event type."""

        def decorator(fn: HandlerFunc) -> HandlerFunc:
            self._register_handler(
                fn,
                raw_event_type=event_type,
                pattern=pattern,
                platform=platform,
            )
            return fn

        return decorator

    # ------------------------------------------------------------------
    # Handler registration internals
    # ------------------------------------------------------------------

    def _register_handler(
        self,
        func: HandlerFunc,
        *,
        event_kind: Optional[str] = None,
        command: Optional[str] = None,
        pattern: Optional[str] = None,
        emoji: Optional[str] = None,
        platform: Optional[str] = None,
        raw_event_type: Optional[str] = None,
    ) -> int:
        handler_id = self._next_id
        self._next_id += 1

        self._handlers[handler_id] = func
        self._handler_filters[handler_id] = {
            "event_kind": event_kind,
            "command": command,
            "pattern": pattern,
            "emoji": emoji,
            "platform": platform,
            "raw_event_type": raw_event_type,
        }
        return handler_id

    def _match_event(self, event: Event) -> list[int]:
        matched = []
        for hid, filt in self._handler_filters.items():
            if filt["event_kind"] is not None and filt["event_kind"] != event.kind:
                continue
            if filt["platform"] is not None and filt["platform"] != event.platform.name:
                continue
            if filt["command"] is not None and event.command != filt["command"]:
                continue
            if filt["pattern"] is not None and not re.search(
                filt["pattern"], event.text
            ):
                continue
            if filt["emoji"] is not None and event.emoji != filt["emoji"]:
                continue
            if (
                filt["raw_event_type"] is not None
                and event.raw_event_type != filt["raw_event_type"]
            ):
                continue
            matched.append(hid)
        return matched

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        event: Event,
        adapter: Adapter,
        matched_ids: Optional[list[int]] = None,
    ) -> None:
        """Dispatch an event to all matching handlers."""
        if matched_ids is None:
            matched_ids = self._match_event(event)

        ctx = Context(event=event, adapter=adapter, bot=self)

        for handler_id in matched_ids:
            handler = self._handlers.get(handler_id)
            if handler is None:
                continue
            try:
                await handler(ctx)
            except Exception:
                logger.exception(
                    "Error in handler %s for event kind=%s",
                    handler.__name__,
                    event.kind,
                )

    async def _dispatch_from_adapter(
        self,
        event: Event,
        adapter: Adapter,
        matched_ids: Optional[list[int]],
    ) -> None:
        """Entry point called by adapters to schedule event dispatch."""
        self._schedule_dispatch(event, adapter, matched_ids)

    def _schedule_dispatch(
        self,
        event: Event,
        adapter: Adapter,
        matched_ids: Optional[list[int]] = None,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(self.dispatch(event, adapter, matched_ids))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for all pending dispatch tasks to complete."""
        if self._pending_tasks:
            _, pending = await asyncio.wait(self._pending_tasks, timeout=timeout)
            for task in pending:
                task.cancel()

    # ------------------------------------------------------------------
    # ASGI app
    # ------------------------------------------------------------------

    def _build_app(self, prefix: str = "") -> Starlette:
        prefix = prefix.rstrip("/")
        routes: list[Route] = []

        for adapter in self._adapters:
            adapter_routes = adapter.routes(self._dispatch_from_adapter)
            for route in adapter_routes:
                if prefix:
                    route.path = prefix + route.path
                routes.append(route)

        return Starlette(routes=routes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if self._app is None:
            self._app = self._build_app()
        await self._app(scope, receive, send)

    def attach(self, app: Any, prefix: str = "/lsmsg") -> None:
        """Mount the bot's webhook routes onto an existing Starlette/FastAPI app."""
        sub_app = self._build_app("")
        app.mount(prefix, sub_app)
