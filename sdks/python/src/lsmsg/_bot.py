"""Bot class: the main entry point for building messaging bots with lsmsg."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import Any, Callable, Coroutine, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lsmsg._types import (
    Event,
    RunChunk,
    RunResult,
    SentMessage,
)

try:
    from lsmsg._lsmsg_core import (
        SlackParser as _SlackParser,
        TeamsParser as _TeamsParser,
        PyHandlerRegistry as _PyHandlerRegistry,
        PyLangGraphClient as _PyLangGraphClient,
        deterministic_thread_id as _deterministic_thread_id,
    )

    _HAS_NATIVE = True
except ImportError:
    _HAS_NATIVE = False

logger = logging.getLogger("lsmsg")

HandlerFunc = Callable[..., Coroutine[Any, Any, Any]]


class Bot:
    """An async-first messaging bot that acts as an ASGI application.

    Example::

        bot = Bot(slack_signing_secret="...", slack_bot_token="...")

        @bot.mention
        async def on_mention(event):
            result = await event.invoke("agent")
            await event.reply(result.text)
    """

    def __init__(
        self,
        *,
        slack_signing_secret: Optional[str] = None,
        slack_bot_token: Optional[str] = None,
        teams_app_id: Optional[str] = None,
        teams_app_password: Optional[str] = None,
        langgraph_url: Optional[str] = None,
        langgraph_api_key: Optional[str] = None,
    ) -> None:
        self.slack_signing_secret = slack_signing_secret
        self.slack_bot_token = slack_bot_token
        self.teams_app_id = teams_app_id
        self.teams_app_password = teams_app_password
        self.langgraph_url = langgraph_url
        self.langgraph_api_key = langgraph_api_key

        # Handler registry: use native if available, else a pure-Python dict
        self._handlers: dict[int, HandlerFunc] = {}
        self._handler_filters: dict[int, dict[str, Any]] = {}
        self._next_id = 1

        if _HAS_NATIVE:
            self._registry = _PyHandlerRegistry()
        else:
            self._registry = None

        self._lg_client: Any = None

        # Track background dispatch tasks to prevent GC and enable graceful shutdown
        self._pending_tasks: set[asyncio.Task[None]] = set()

        # Build ASGI app
        self._prefix = ""
        self._app: Optional[Starlette] = None

    # ------------------------------------------------------------------
    # Decorators
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
        if self._registry is not None:
            handler_id = self._registry.register(
                event_kind=event_kind,
                command=command,
                pattern=pattern,
                emoji=emoji,
                platform=platform,
                raw_event_type=raw_event_type,
            )
        else:
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

    def _match_event_python(self, event: Event) -> list[int]:
        """Pure-Python fallback for handler matching when native module is unavailable."""
        import re

        matched = []
        for hid, filt in self._handler_filters.items():
            if filt["event_kind"] is not None and filt["event_kind"] != event.kind:
                continue
            if filt["platform"] is not None and filt["platform"] != event.platform.name:
                continue
            if filt["command"] is not None:
                if event.command != filt["command"]:
                    continue
            if filt["pattern"] is not None:
                if not re.search(filt["pattern"], event.text):
                    continue
            if filt["emoji"] is not None:
                if event.emoji != filt["emoji"]:
                    continue
            if filt["raw_event_type"] is not None:
                if event.raw_event_type != filt["raw_event_type"]:
                    continue
            matched.append(hid)
        return matched

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: Event) -> None:
        """Dispatch an event to all matching handlers."""
        # Bind bot to event so event.reply() etc. work
        bound_event = replace(event, _bot=self)

        if self._registry is not None:
            # Use native matching via Rust
            event_dict = _event_to_dict(event)
            try:
                matched_ids = await asyncio.to_thread(
                    self._registry.match_event, event_dict
                )
            except Exception:
                logger.exception("Error matching event via native registry")
                matched_ids = []
        else:
            matched_ids = self._match_event_python(event)

        for handler_id in matched_ids:
            handler = self._handlers.get(handler_id)
            if handler is None:
                continue
            try:
                await handler(bound_event)
            except Exception:
                logger.exception(
                    "Error in handler %s for event kind=%s",
                    handler.__name__,
                    event.kind,
                )

    def _schedule_dispatch(self, event: Event) -> asyncio.Task[None]:
        """Schedule dispatch as a tracked background task."""
        task = asyncio.create_task(self.dispatch(event))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        return task

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for all pending dispatch tasks to complete (for graceful shutdown)."""
        if self._pending_tasks:
            _, pending = await asyncio.wait(self._pending_tasks, timeout=timeout)
            for task in pending:
                task.cancel()

    # ------------------------------------------------------------------
    # ASGI app
    # ------------------------------------------------------------------

    def _build_app(self, prefix: str = "") -> Starlette:
        prefix = prefix.rstrip("/")

        async def slack_events(request: Request) -> Response:
            return await self._handle_slack_webhook(request)

        async def teams_events(request: Request) -> Response:
            return await self._handle_teams_webhook(request)

        routes = [
            Route(f"{prefix}/slack/events", slack_events, methods=["POST"]),
            Route(f"{prefix}/teams/events", teams_events, methods=["POST"]),
        ]
        return Starlette(routes=routes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if self._app is None:
            self._app = self._build_app(self._prefix)
        await self._app(scope, receive, send)

    def attach(self, app: Any, prefix: str = "/lsmsg") -> None:
        """Mount the bot's webhook routes onto an existing Starlette/FastAPI app."""
        self._prefix = prefix
        sub_app = self._build_app(prefix)
        app.mount(prefix, sub_app)

    # ------------------------------------------------------------------
    # Webhook handlers
    # ------------------------------------------------------------------

    async def _handle_slack_webhook(self, request: Request) -> Response:
        body = await request.body()
        content_type = request.headers.get("content-type", "application/json")

        # Signature verification
        if self.slack_signing_secret:
            timestamp = request.headers.get("x-slack-request-timestamp", "")
            signature = request.headers.get("x-slack-signature", "")
            if timestamp and signature:
                if _HAS_NATIVE:
                    valid = await asyncio.to_thread(
                        _SlackParser.verify_signature,
                        self.slack_signing_secret,
                        timestamp,
                        signature,
                        body,
                    )
                    if not valid:
                        return JSONResponse(
                            {"error": "invalid signature"}, status_code=401
                        )

        # Parse the webhook
        if _HAS_NATIVE:
            result = await asyncio.to_thread(
                _SlackParser.parse_webhook, body, content_type
            )
        else:
            result = await asyncio.to_thread(
                _parse_slack_webhook_python, body, content_type
            )

        result_type = result.get("type")

        if result_type == "challenge":
            return JSONResponse({"challenge": result["challenge"]})

        if result_type == "ignored":
            return JSONResponse({"ok": True})

        if result_type == "event":
            event = Event.from_dict(result["event"])
            self._schedule_dispatch(event)
            return JSONResponse({"ok": True})

        return JSONResponse({"ok": True})

    async def _handle_teams_webhook(self, request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        if _HAS_NATIVE:
            result = await asyncio.to_thread(_TeamsParser.parse_webhook, payload)
        else:
            result = await asyncio.to_thread(_parse_teams_webhook_python, payload)

        if result is None:
            return JSONResponse({"ok": True})

        event = Event.from_dict(result)
        self._schedule_dispatch(event)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------
    # Agent invocation helpers
    # ------------------------------------------------------------------

    def _get_lg_client(self) -> Any:
        if self._lg_client is None:
            if not self.langgraph_url:
                raise RuntimeError(
                    "langgraph_url must be set on the Bot to invoke agents"
                )
            if _HAS_NATIVE:
                self._lg_client = _PyLangGraphClient(
                    self.langgraph_url, self.langgraph_api_key
                )
            else:
                raise RuntimeError("Native extension required for LangGraph client")
        return self._lg_client

    async def invoke_agent(
        self, *, agent: str, event: Event, **kwargs: Any
    ) -> RunResult:
        client = self._get_lg_client()
        thread_id = event.internal_thread_id or ""
        input_data = kwargs.get(
            "input", {"messages": [{"role": "user", "content": event.text}]}
        )
        config = kwargs.get("config")
        metadata = kwargs.get("metadata")
        run_id = await asyncio.to_thread(
            client.create_run, agent, thread_id, input_data, config, metadata
        )
        result_dict = await asyncio.to_thread(client.wait_run, thread_id, run_id)
        return RunResult.from_dict(result_dict)

    async def stream_agent(
        self, *, agent: str, event: Event, **kwargs: Any
    ) -> list[RunChunk]:
        client = self._get_lg_client()
        thread_id = event.internal_thread_id or ""
        input_data = kwargs.get(
            "input", {"messages": [{"role": "user", "content": event.text}]}
        )
        config = kwargs.get("config")
        metadata = kwargs.get("metadata")
        chunk_dicts = await asyncio.to_thread(
            client.stream_new_run,
            agent,
            thread_id,
            input_data,
            config,
            metadata,
        )
        return [RunChunk.from_dict(c) for c in chunk_dicts]

    async def start_agent(self, *, agent: str, event: Event, **kwargs: Any) -> str:
        client = self._get_lg_client()
        thread_id = event.internal_thread_id or ""
        input_data = kwargs.get(
            "input", {"messages": [{"role": "user", "content": event.text}]}
        )
        config = kwargs.get("config")
        metadata = kwargs.get("metadata")
        run_id = await asyncio.to_thread(
            client.create_run, agent, thread_id, input_data, config, metadata
        )
        return run_id

    # ------------------------------------------------------------------
    # Message sending (stubs - platform-specific implementations)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        text: str,
    ) -> SentMessage:
        """Send a message. Override or extend for real platform API calls."""
        logger.info(
            "send_message platform=%s channel=%s thread=%s text=%s",
            platform,
            channel_id,
            thread_id,
            text[:50],
        )
        return SentMessage(id="pending", platform=platform, channel_id=channel_id)

    async def send_ephemeral(
        self,
        *,
        platform: str,
        channel_id: str,
        thread_id: str,
        user_id: str,
        text: str,
    ) -> SentMessage:
        """Send an ephemeral message visible only to the user."""
        logger.info(
            "send_ephemeral platform=%s channel=%s user=%s text=%s",
            platform,
            channel_id,
            user_id,
            text[:50],
        )
        return SentMessage(id="pending", platform=platform, channel_id=channel_id)


# --------------------------------------------------------------------------
# Pure-Python webhook parsing fallbacks (used when native ext not available)
# --------------------------------------------------------------------------


def _parse_slack_webhook_python(body: bytes, content_type: str) -> dict[str, Any]:
    """Minimal pure-Python Slack webhook parser for use without native ext."""
    import re
    from urllib.parse import parse_qs

    if content_type.startswith("application/x-www-form-urlencoded"):
        decoded = body.decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(decoded).items()}

        # Interactive payload
        if "payload" in form:
            payload = json.loads(form["payload"])
            interaction_type = payload.get("type", "")
            if interaction_type not in (
                "block_actions",
                "shortcut",
                "message_action",
                "view_submission",
            ):
                return {"type": "ignored"}
            user = payload.get("user", {})
            channel = payload.get("channel", {})
            team = payload.get("team", {})
            message = payload.get("message", {})
            root_id = (
                message.get("thread_ts")
                or message.get("ts")
                or payload.get("trigger_id", "unknown")
            )
            return {
                "type": "event",
                "event": {
                    "kind": "message",
                    "platform": _slack_caps_dict(),
                    "workspace_id": team.get("id", "unknown"),
                    "channel_id": channel.get("id", "unknown"),
                    "thread_id": root_id,
                    "message_id": message.get("client_msg_id")
                    or message.get("ts", root_id),
                    "user": {
                        "id": user.get("id", "unknown"),
                        "name": user.get("username"),
                    },
                    "text": message.get("text", ""),
                    "raw": payload,
                },
            }

        # Slash command
        command = form.get("command", "")
        if not command:
            return {"type": "ignored"}
        trigger_id = form.get("trigger_id", "")
        thread_ts = form.get("thread_ts", "")
        root_id = thread_ts or trigger_id or "unknown"
        return {
            "type": "event",
            "event": {
                "kind": "command",
                "platform": _slack_caps_dict(),
                "workspace_id": form.get("team_id", "unknown"),
                "channel_id": form.get("channel_id", "unknown"),
                "thread_id": root_id,
                "message_id": trigger_id or root_id,
                "user": {
                    "id": form.get("user_id", "unknown"),
                    "name": form.get("user_name"),
                },
                "text": form.get("text", ""),
                "command": command,
                "raw": form,
            },
        }

    # JSON payloads
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"type": "ignored"}

    if payload.get("type") == "url_verification":
        return {"type": "challenge", "challenge": payload.get("challenge", "")}

    if payload.get("type") != "event_callback":
        return {"type": "ignored"}

    event = payload.get("event", {})
    if not event:
        return {"type": "ignored"}

    # Skip bot messages
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return {"type": "ignored"}

    event_type = event.get("type", "")
    raw_text = event.get("text", "")
    text = re.sub(r"<@[^>]+>\s*", "", raw_text).strip()

    is_mention = event_type == "app_mention" or bool(re.search(r"<@[^>]+>", raw_text))

    if event_type == "reaction_added":
        kind = "reaction"
        emoji = event.get("reaction", "")
    elif is_mention:
        kind = "mention"
        emoji = None
    else:
        kind = "message"
        emoji = None

    root_id = event.get("thread_ts") or event.get("ts", "unknown")
    msg_id = event.get("client_msg_id") or event.get("ts", root_id)
    workspace_id = payload.get("team_id") or event.get("team", "unknown")

    result_event: dict[str, Any] = {
        "kind": kind,
        "platform": _slack_caps_dict(),
        "workspace_id": workspace_id,
        "channel_id": event.get("channel", "unknown"),
        "thread_id": root_id,
        "message_id": msg_id,
        "user": {"id": event.get("user", "unknown")},
        "text": text,
        "raw": payload,
    }
    if emoji is not None:
        result_event["emoji"] = emoji

    return {"type": "event", "event": result_event}


def _parse_teams_webhook_python(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Minimal pure-Python Teams webhook parser."""
    import re

    activity_type = payload.get("type", "")

    if activity_type == "messageReaction":
        reactions = payload.get("reactionsAdded", [])
        if not reactions:
            return None
        emoji = reactions[0].get("type", "")
        from_user = payload.get("from", {})
        conversation = payload.get("conversation", {})
        channel_data = payload.get("channelData", {})
        tenant = channel_data.get("tenant", {})
        team = channel_data.get("team", {})
        conv_id = conversation.get("id", "unknown")
        root_id = payload.get("replyToId") or conv_id
        return {
            "kind": "reaction",
            "platform": _teams_caps_dict(),
            "workspace_id": tenant.get("id", "unknown"),
            "channel_id": team.get("id", conv_id),
            "thread_id": root_id,
            "message_id": payload.get("id", root_id),
            "user": {
                "id": from_user.get("id", "unknown"),
                "name": from_user.get("name"),
            },
            "text": "",
            "emoji": emoji,
            "raw": payload,
        }

    if activity_type != "message":
        return None

    from_user = payload.get("from", {})
    conversation = payload.get("conversation", {})
    channel_data = payload.get("channelData", {})
    tenant = channel_data.get("tenant", {})
    team = channel_data.get("team", {})

    raw_text = payload.get("text", "")
    text = re.sub(r"(?i)<at>.*?</at>", "", raw_text)
    text = re.sub(r"\s+", " ", text.strip())

    entities = payload.get("entities", [])
    has_mention = any(e.get("type") == "mention" for e in entities)
    is_mention = has_mention or text != raw_text

    conv_id = conversation.get("id", "unknown")
    root_id = payload.get("replyToId") or conv_id or payload.get("id", "unknown")

    kind = "mention" if is_mention else "message"

    return {
        "kind": kind,
        "platform": _teams_caps_dict(),
        "workspace_id": tenant.get("id", conversation.get("tenantId", "unknown")),
        "channel_id": team.get("id", conv_id),
        "thread_id": root_id,
        "message_id": payload.get("id", root_id),
        "user": {
            "id": from_user.get("id", "unknown"),
            "name": from_user.get("name"),
        },
        "text": text,
        "raw": payload,
    }


def _slack_caps_dict() -> dict[str, Any]:
    return {
        "name": "slack",
        "ephemeral": True,
        "threads": True,
        "reactions": True,
        "streaming": True,
        "modals": True,
        "typing_indicator": True,
    }


def _teams_caps_dict() -> dict[str, Any]:
    return {
        "name": "teams",
        "ephemeral": False,
        "threads": True,
        "reactions": False,
        "streaming": False,
        "modals": False,
        "typing_indicator": True,
    }


def _event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an Event back to a dict suitable for the native registry."""
    platform_dict = {
        "name": event.platform.name,
        "ephemeral": event.platform.ephemeral,
        "threads": event.platform.threads,
        "reactions": event.platform.reactions,
        "streaming": event.platform.streaming,
        "modals": event.platform.modals,
        "typing_indicator": event.platform.typing_indicator,
    }
    d: dict[str, Any] = {
        "kind": event.kind,
        "platform": platform_dict,
        "workspace_id": event.workspace_id,
        "channel_id": event.channel_id,
        "thread_id": event.thread_id,
        "message_id": event.message_id,
        "user": {
            "id": event.user.id,
            "name": event.user.name,
            "email": event.user.email,
        },
        "text": event.text,
        "raw": event.raw if event.raw is not None else None,
    }
    if event.command is not None:
        d["command"] = event.command
    if event.emoji is not None:
        d["emoji"] = event.emoji
    if event.raw_event_type is not None:
        d["raw_event_type"] = event.raw_event_type
    return d
