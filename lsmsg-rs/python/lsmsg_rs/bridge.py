from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import re
import time
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Literal, Mapping, TypeAlias
from urllib.parse import parse_qs

Provider: TypeAlias = Literal["slack", "teams"]
EventType: TypeAlias = Literal["mention", "message", "command", "unknown"]


@dataclass(frozen=True, slots=True)
class SlackRouteCtx:
    provider: Literal["slack"]
    event_type: EventType
    workspace_id: str
    channel_id: str
    root_thread_id: str
    message_id: str
    user_id: str
    text: str
    assistant_hint: str | None
    command: str | None
    raw: Mapping[str, Any]
    headers: Mapping[str, str]

    @property
    def thread_key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.workspace_id, self.channel_id, self.root_thread_id)


@dataclass(frozen=True, slots=True)
class TeamsRouteCtx:
    provider: Literal["teams"]
    event_type: EventType
    workspace_id: str
    channel_id: str
    root_thread_id: str
    message_id: str
    user_id: str
    text: str
    assistant_hint: str | None
    raw: Mapping[str, Any]
    headers: Mapping[str, str]

    @property
    def thread_key(self) -> tuple[str, str, str, str]:
        return (self.provider, self.workspace_id, self.channel_id, self.root_thread_id)


RouteCtx: TypeAlias = SlackRouteCtx | TeamsRouteCtx


@dataclass(frozen=True, slots=True)
class SlackAck:
    text: str | None = None
    response_type: Literal["ephemeral", "in_channel"] = "ephemeral"
    blocks: tuple[Mapping[str, Any], ...] | None = None
    replace_original: bool | None = None
    delete_original: bool | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.text is not None:
            payload["text"] = self.text
        if self.response_type:
            payload["response_type"] = self.response_type
        if self.blocks is not None:
            payload["blocks"] = [dict(block) for block in self.blocks]
        if self.replace_original is not None:
            payload["replace_original"] = self.replace_original
        if self.delete_original is not None:
            payload["delete_original"] = self.delete_original
        return payload


HandlerResult: TypeAlias = SlackAck | None
RouteHandler: TypeAlias = Callable[[RouteCtx], Awaitable[HandlerResult] | HandlerResult]


@dataclass(frozen=True, slots=True)
class _RouteRegistration:
    event_type: EventType | Literal["*"]
    provider: Provider | None
    command: str | None
    handler: RouteHandler


class ChatBridge:
    """Decorator-based unified webhook bridge for Slack + Teams.

    This bridge is async-first and Starlette-native. It can either provide its
    own ASGI app (`asgi_app`) or attach routes to an existing Starlette app
    (`register_routes` / `mount`).
    """

    def __init__(
        self,
        *,
        slack_signing_secret: str | None = None,
        background_dispatch: bool = True,
    ) -> None:
        self._slack_signing_secret = _clean_optional(slack_signing_secret)
        self._background_dispatch = background_dispatch
        self._routes: list[_RouteRegistration] = []
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def on_mention(self, *, provider: Provider | None = None) -> Callable[[RouteHandler], RouteHandler]:
        return self._register(event_type="mention", provider=provider, command=None)

    def on_message(self, *, provider: Provider | None = None) -> Callable[[RouteHandler], RouteHandler]:
        return self._register(event_type="message", provider=provider, command=None)

    def on_command(self, command: str) -> Callable[[RouteHandler], RouteHandler]:
        normalized = command.strip()
        if not normalized:
            raise ValueError("command must be a non-empty string")
        return self._register(event_type="command", provider="slack", command=normalized)

    def on_event(
        self,
        *,
        event_type: EventType | Literal["*"] = "*",
        provider: Provider | None = None,
    ) -> Callable[[RouteHandler], RouteHandler]:
        return self._register(event_type=event_type, provider=provider, command=None)

    def asgi_app(self):
        Starlette, Route = _starlette_imports()
        return Starlette(
            routes=[
                Route("/slack/events", endpoint=self.slack_webhook, methods=["POST"]),
                Route("/teams/events", endpoint=self.teams_webhook, methods=["POST"]),
            ]
        )

    def register_routes(self, app: Any, *, prefix: str = "/chat") -> None:
        normalized = _normalize_prefix(prefix)
        app.add_route(f"{normalized}/slack/events", self.slack_webhook, methods=["POST"])
        app.add_route(f"{normalized}/teams/events", self.teams_webhook, methods=["POST"])

    def mount(self, app: Any, *, path: str = "/chat") -> None:
        normalized = _normalize_prefix(path)
        app.mount(normalized, self.asgi_app())

    async def dispatch(self, ctx: RouteCtx) -> int:
        matched, _ = await self._dispatch_internal(ctx)
        return matched

    async def dispatch_with_ack(self, ctx: RouteCtx) -> tuple[int, SlackAck | None]:
        return await self._dispatch_internal(ctx)

    async def _dispatch_internal(self, ctx: RouteCtx) -> tuple[int, SlackAck | None]:
        matched = 0
        ack: SlackAck | None = None
        for route in self._routes:
            if route.provider is not None and route.provider != ctx.provider:
                continue
            if route.event_type != "*" and route.event_type != ctx.event_type:
                continue
            if route.command is not None:
                if not isinstance(ctx, SlackRouteCtx):
                    continue
                if (ctx.command or "") != route.command:
                    continue

            matched += 1
            result = route.handler(ctx)
            if inspect.isawaitable(result):
                result = await result
            if ack is None and isinstance(result, SlackAck):
                ack = result

        return matched, ack

    async def slack_webhook(self, request: Any):
        JSONResponse, PlainTextResponse = _response_imports()

        body = await request.body()
        if self._slack_signing_secret is not None:
            if not _verify_slack_signature(
                signing_secret=self._slack_signing_secret,
                headers=request.headers,
                body=body,
            ):
                return PlainTextResponse("invalid slack signature", status_code=401)

        content_type = (request.headers.get("content-type") or "").lower()
        if content_type.startswith("application/x-www-form-urlencoded"):
            ctx = _parse_slack_form(body=body, headers=request.headers)
            if ctx is None:
                return JSONResponse({"ok": True})
            return await self._dispatch_webhook(ctx)

        payload: dict[str, Any]
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return PlainTextResponse("invalid json", status_code=400)

        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            return JSONResponse({"challenge": challenge})

        ctx = _parse_slack_event(payload=payload, headers=request.headers)
        if ctx is None:
            return JSONResponse({"ok": True})
        return await self._dispatch_webhook(ctx)

    async def teams_webhook(self, request: Any):
        JSONResponse, PlainTextResponse = _response_imports()

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return PlainTextResponse("invalid json", status_code=400)

        ctx = _parse_teams_event(payload=payload, headers=request.headers)
        if ctx is None:
            return JSONResponse({"ok": True})
        return await self._dispatch_webhook(ctx)

    def _register(
        self,
        *,
        event_type: EventType | Literal["*"],
        provider: Provider | None,
        command: str | None,
    ) -> Callable[[RouteHandler], RouteHandler]:
        def decorator(handler: RouteHandler) -> RouteHandler:
            if not callable(handler):
                raise TypeError("handler must be callable")
            self._routes.append(
                _RouteRegistration(
                    event_type=event_type,
                    provider=provider,
                    command=command,
                    handler=handler,
                )
            )
            return handler

        return decorator

    async def _dispatch_webhook(self, ctx: RouteCtx):
        JSONResponse, _ = _response_imports()

        # Slash commands need inline handler execution when the handler returns
        # a custom Slack ack payload.
        inline_dispatch = isinstance(ctx, SlackRouteCtx) and ctx.event_type == "command"
        if self._background_dispatch and not inline_dispatch:
            task = asyncio.create_task(self._dispatch_internal(ctx))
            self._pending_tasks.add(task)
            task.add_done_callback(self._on_task_done)
            return JSONResponse({"ok": True})

        _, ack = await self._dispatch_internal(ctx)
        if isinstance(ctx, SlackRouteCtx) and ack is not None:
            return JSONResponse(ack.to_payload())
        return JSONResponse({"ok": True})

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._pending_tasks.discard(task)
        try:
            task.result()
        except Exception:
            # The task has already failed asynchronously; keep webhook responses fast.
            # Users can register middleware/logging around handlers for richer reporting.
            pass


def _starlette_imports():
    try:
        from starlette.applications import Starlette
        from starlette.routing import Route
    except ImportError as exc:  # pragma: no cover - dependency/import wiring only
        raise RuntimeError(
            "Starlette is required for ChatBridge. Install with: pip install starlette"
        ) from exc
    return Starlette, Route


def _response_imports():
    try:
        from starlette.responses import JSONResponse, PlainTextResponse
    except ImportError as exc:  # pragma: no cover - dependency/import wiring only
        raise RuntimeError(
            "Starlette is required for ChatBridge. Install with: pip install starlette"
        ) from exc
    return JSONResponse, PlainTextResponse


def _normalize_prefix(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("prefix/path must be a non-empty string")
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    if trimmed != "/":
        trimmed = trimmed.rstrip("/")
    return trimmed


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _headers_map(headers: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType({k.lower(): v for k, v in headers.items()})


def _proxy_json(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(payload))


def _parse_slack_form(body: bytes, headers: Mapping[str, str]) -> SlackRouteCtx | None:
    decoded = body.decode("utf-8")
    form = parse_qs(decoded, keep_blank_values=True)

    if "payload" in form:
        raw_payload = _first(form.get("payload"), "{}")
        try:
            payload: dict[str, Any] = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
        return _parse_slack_interaction(payload=payload, headers=headers)

    command = _first(form.get("command"), "").strip()
    if not command:
        return None

    text = _first(form.get("text"), "")
    workspace_id = _first(form.get("team_id"), "unknown")
    channel_id = _first(form.get("channel_id"), "unknown")
    trigger_id = _first(form.get("trigger_id"), "")
    message_ts = _first(form.get("message_ts"), "")
    thread_ts = _first(form.get("thread_ts"), "")
    root_thread_id = thread_ts or message_ts or trigger_id or f"slash-{int(time.time() * 1000)}"
    message_id = trigger_id or f"slash-{int(time.time() * 1000)}"
    user_id = _first(form.get("user_id"), "unknown")

    return SlackRouteCtx(
        provider="slack",
        event_type="command",
        workspace_id=workspace_id,
        channel_id=channel_id,
        root_thread_id=root_thread_id,
        message_id=message_id,
        user_id=user_id,
        text=text,
        assistant_hint=_assistant_hint_from_text(text),
        command=command,
        raw=_proxy_json({k: v[0] if len(v) == 1 else v for k, v in form.items()}),
        headers=_headers_map(headers),
    )


def _parse_slack_interaction(payload: dict[str, Any], headers: Mapping[str, str]) -> SlackRouteCtx | None:
    interaction_type = str(payload.get("type") or "")
    if interaction_type not in {"block_actions", "shortcut", "message_action", "view_submission"}:
        return None

    user = payload.get("user") or {}
    channel = payload.get("channel") or {}
    team = payload.get("team") or {}
    message = payload.get("message") or {}

    text = str(message.get("text") or "")
    root_thread_id = str(message.get("thread_ts") or message.get("ts") or payload.get("trigger_id") or f"interaction-{int(time.time() * 1000)}")

    return SlackRouteCtx(
        provider="slack",
        event_type="message",
        workspace_id=str(team.get("id") or "unknown"),
        channel_id=str(channel.get("id") or "unknown"),
        root_thread_id=root_thread_id,
        message_id=str(message.get("client_msg_id") or message.get("ts") or payload.get("trigger_id") or root_thread_id),
        user_id=str(user.get("id") or "unknown"),
        text=text,
        assistant_hint=_assistant_hint_from_text(text),
        command=None,
        raw=_proxy_json(payload),
        headers=_headers_map(headers),
    )


def _parse_slack_event(payload: dict[str, Any], headers: Mapping[str, str]) -> SlackRouteCtx | None:
    if payload.get("type") != "event_callback":
        return None

    event = payload.get("event")
    if not isinstance(event, dict):
        return None

    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return None

    event_type_raw = str(event.get("type") or "")
    text = str(event.get("text") or "")
    mention = event_type_raw == "app_mention" or bool(re.search(r"<@[^>]+>", text))
    event_type: EventType = "mention" if mention else "message"

    workspace_id = str(payload.get("team_id") or event.get("team") or "unknown")
    channel_id = str(event.get("channel") or "unknown")
    root_thread_id = str(event.get("thread_ts") or event.get("ts") or f"event-{int(time.time() * 1000)}")
    message_id = str(event.get("client_msg_id") or event.get("ts") or root_thread_id)
    user_id = str(event.get("user") or "unknown")

    return SlackRouteCtx(
        provider="slack",
        event_type=event_type,
        workspace_id=workspace_id,
        channel_id=channel_id,
        root_thread_id=root_thread_id,
        message_id=message_id,
        user_id=user_id,
        text=text,
        assistant_hint=_assistant_hint_from_text(text),
        command=None,
        raw=_proxy_json(payload),
        headers=_headers_map(headers),
    )


def _parse_teams_event(payload: dict[str, Any], headers: Mapping[str, str]) -> TeamsRouteCtx | None:
    activity_type = str(payload.get("type") or "")
    if activity_type != "message":
        return None

    from_user = payload.get("from") or {}
    conversation = payload.get("conversation") or {}
    channel_data = payload.get("channelData") or {}
    tenant = channel_data.get("tenant") or {}
    team = channel_data.get("team") or {}

    text = str(payload.get("text") or "")
    clean_text = _strip_teams_mentions(text)
    entities = payload.get("entities")
    has_mention_entity = isinstance(entities, list) and any(
        isinstance(entity, dict) and entity.get("type") == "mention" for entity in entities
    )
    mention = has_mention_entity or clean_text != text
    event_type: EventType = "mention" if mention else "message"

    conversation_id = str(conversation.get("id") or "unknown")
    root_thread_id = str(payload.get("replyToId") or conversation_id or payload.get("id") or f"teams-{int(time.time() * 1000)}")
    message_id = str(payload.get("id") or root_thread_id)

    workspace_id = str(tenant.get("id") or conversation.get("tenantId") or "unknown")
    channel_id = str(team.get("id") or conversation_id)
    user_id = str(from_user.get("id") or "unknown")

    return TeamsRouteCtx(
        provider="teams",
        event_type=event_type,
        workspace_id=workspace_id,
        channel_id=channel_id,
        root_thread_id=root_thread_id,
        message_id=message_id,
        user_id=user_id,
        text=clean_text,
        assistant_hint=_assistant_hint_from_text(clean_text),
        raw=_proxy_json(payload),
        headers=_headers_map(headers),
    )


def _strip_teams_mentions(text: str) -> str:
    without_tags = re.sub(r"<at>.*?</at>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", without_tags).strip()


def _assistant_hint_from_text(text: str) -> str | None:
    cleaned = text.strip()
    while True:
        updated = re.sub(r"^\s*(?:<@[^>]+>|@[^\s]+|<at>.*?</at>)\s*", "", cleaned, count=1, flags=re.IGNORECASE)
        if updated == cleaned:
            break
        cleaned = updated.strip()

    if not cleaned:
        return None

    token = cleaned.split(maxsplit=1)[0].strip()
    token = token.lstrip("/@")
    return token or None


def _verify_slack_signature(
    *,
    signing_secret: str,
    headers: Mapping[str, str],
    body: bytes,
) -> bool:
    timestamp = headers.get("x-slack-request-timestamp") or headers.get("X-Slack-Request-Timestamp")
    signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
    if not timestamp or not signature:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    # Slack recommends rejecting messages older than 5 minutes.
    if abs(int(time.time()) - ts_int) > 60 * 5:
        return False

    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def _first(values: list[str] | None, default: str) -> str:
    if not values:
        return default
    return values[0]


__all__ = [
    "ChatBridge",
    "EventType",
    "Provider",
    "RouteCtx",
    "SlackAck",
    "SlackRouteCtx",
    "TeamsRouteCtx",
]
