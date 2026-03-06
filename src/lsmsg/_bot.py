from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from dataclasses import dataclass, replace
from typing import (
    Any,
    Awaitable,
    Callable,
    Mapping,
    TypeAlias,
)

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ._capabilities import Platform
from ._errors import LsmsgError
from ._events import (
    BaseEvent,
    CommandEvent,
    MentionEvent,
    MessageEvent,
    RawEvent,
    ReactionEvent,
)
from ._platforms import Discord, GChat, GitHub, Linear, Slack, Teams, Telegram
from ._reply import MessageSender
from ._run import RunBackend
from ._slack import parse_slack_webhook, verify_slack_signature
from ._teams import parse_teams_webhook

EventHandler: TypeAlias = Callable[..., Awaitable[Any]]
ErrorHandler: TypeAlias = Callable[[BaseEvent, Exception], Awaitable[None]]
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class _HandlerRegistration:
    event_type: type[BaseEvent]
    handler: EventHandler
    command: str | None = None
    pattern: re.Pattern[str] | None = None
    emoji: str | None = None
    raw_event_type: str | None = None
    platform: Platform | None = None


class Bot:
    def __init__(
        self,
        *,
        slack: Slack | None = None,
        teams: Teams | None = None,
        discord: Discord | None = None,
        telegram: Telegram | None = None,
        github: GitHub | None = None,
        linear: Linear | None = None,
        gchat: GChat | None = None,
        run_backend: RunBackend | None = None,
        message_sender: MessageSender | None = None,
        on_error: ErrorHandler | None = None,
        max_pending_tasks: int = 1_024,
        max_body_bytes: int = 1_048_576,
        allow_unauthenticated_teams: bool = False,
    ) -> None:
        unsupported: list[str] = []
        if discord is not None:
            unsupported.append("discord")
        if telegram is not None:
            unsupported.append("telegram")
        if github is not None:
            unsupported.append("github")
        if linear is not None:
            unsupported.append("linear")
        if gchat is not None:
            unsupported.append("gchat")
        if unsupported:
            joined = ", ".join(sorted(unsupported))
            raise NotImplementedError(
                f"Bot currently supports only Slack and Teams webhooks. Unsupported configs: {joined}"
            )
        if max_pending_tasks <= 0:
            raise ValueError("max_pending_tasks must be positive")
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self._slack = slack
        self._teams = teams
        if run_backend is None:
            from ._langgraph_backend import LangGraphRunBackend

            run_backend = LangGraphRunBackend()
        self._run_backend = run_backend
        self._message_sender = message_sender or _NoopMessageSender()
        self._on_error = on_error
        self._handlers: list[_HandlerRegistration] = []
        self._command_ack: dict[str, str | bool] = {}
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self._max_pending_tasks = max_pending_tasks
        self._max_body_bytes = max_body_bytes
        self._teams_validator = (
            _TeamsTokenValidator(teams.app_id) if teams is not None else None
        )
        self._allow_unauthenticated_teams = allow_unauthenticated_teams
        self._app: Starlette | None = None

    # -- Decorators --

    @property
    def mention(self) -> _Decorator:
        return _Decorator(self, MentionEvent)

    @property
    def message(self) -> _Decorator:
        return _Decorator(self, MessageEvent)

    @property
    def reaction(self) -> _ReactionDecorator:
        return _ReactionDecorator(self)

    def command(
        self,
        name: str,
        *,
        ack: str | bool = True,
    ) -> Callable[[EventHandler], EventHandler]:
        normalized = name.strip()
        if not normalized:
            raise ValueError("command name must be non-empty")
        if ack is not False:
            self._command_ack[normalized] = ack if isinstance(ack, str) else True
        else:
            self._command_ack[normalized] = False

        def decorator(handler: EventHandler) -> EventHandler:
            self._handlers.append(
                _HandlerRegistration(
                    event_type=CommandEvent,
                    handler=handler,
                    command=normalized,
                )
            )
            return handler

        return decorator

    def on(
        self,
        event_type: str,
        *,
        platform: Platform | None = None,
    ) -> Callable[[EventHandler], EventHandler]:
        def decorator(handler: EventHandler) -> EventHandler:
            self._handlers.append(
                _HandlerRegistration(
                    event_type=RawEvent,
                    handler=handler,
                    raw_event_type=event_type,
                    platform=platform,
                )
            )
            return handler

        return decorator

    def _register(
        self,
        event_type: type[BaseEvent],
        handler: EventHandler,
        *,
        pattern: str | None = None,
        emoji: str | None = None,
        platform: Platform | None = None,
    ) -> None:
        compiled = re.compile(pattern) if pattern else None
        self._handlers.append(
            _HandlerRegistration(
                event_type=event_type,
                handler=handler,
                pattern=compiled,
                emoji=emoji,
                platform=platform,
            )
        )

    # -- ASGI --

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        app = self._get_app()
        await app(scope, receive, send)

    def attach(self, app: Any, *, prefix: str = "/chat") -> None:
        normalized = _normalize_prefix(prefix)
        routes = self._build_routes(normalized)
        for path, endpoint, methods in routes:
            app.add_route(path, endpoint, methods=methods)

    def _get_app(self) -> Starlette:
        if self._app is None:
            routes_raw = self._build_routes("")
            routes = [Route(path, endpoint=ep, methods=m) for path, ep, m in routes_raw]
            self._app = Starlette(routes=routes)
        return self._app

    def _build_routes(self, prefix: str) -> list[tuple[str, Any, list[str]]]:
        routes: list[tuple[str, Any, list[str]]] = []
        if self._slack is not None:
            routes.append((f"{prefix}/slack/events", self._slack_webhook, ["POST"]))
        if self._teams is not None:
            routes.append((f"{prefix}/teams/events", self._teams_webhook, ["POST"]))
        return routes

    # -- Webhook Handlers --

    async def _slack_webhook(self, request: Request) -> Response:
        if _is_content_length_too_large(request.headers, self._max_body_bytes):
            return PlainTextResponse("payload too large", status_code=413)
        body = await request.body()
        if len(body) > self._max_body_bytes:
            return PlainTextResponse("payload too large", status_code=413)

        if self._slack is not None and self._slack.signing_secret:
            if not verify_slack_signature(
                signing_secret=self._slack.signing_secret,
                headers=request.headers,
                body=body,
            ):
                return PlainTextResponse("invalid signature", status_code=401)

        result = parse_slack_webhook(body, request.headers)

        if result is None:
            return JSONResponse({"ok": True})

        if isinstance(result, dict):
            return JSONResponse(result)

        event = self._inject_backends(result)

        if isinstance(event, CommandEvent):
            return await self._handle_command_event(event)

        if not self._spawn(self._dispatch(event)):
            return JSONResponse(
                {"ok": False, "error": "too_many_pending_events"}, status_code=429
            )
        return JSONResponse({"ok": True})

    async def _teams_webhook(self, request: Request) -> Response:
        if not self._allow_unauthenticated_teams:
            if self._teams_validator is None:
                return PlainTextResponse("teams auth not configured", status_code=503)
            try:
                valid_auth = self._teams_validator.validate_authorization(
                    request.headers.get("authorization")
                )
            except RuntimeError as exc:
                return PlainTextResponse(str(exc), status_code=500)
            if not valid_auth:
                return PlainTextResponse("invalid teams authorization", status_code=401)
        if _is_content_length_too_large(request.headers, self._max_body_bytes):
            return PlainTextResponse("payload too large", status_code=413)
        body = await request.body()
        if len(body) > self._max_body_bytes:
            return PlainTextResponse("payload too large", status_code=413)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return PlainTextResponse("invalid json", status_code=400)

        event = parse_teams_webhook(payload)
        if event is None:
            return JSONResponse({"ok": True})

        event = self._inject_backends(event)
        if not self._spawn(self._dispatch(event)):
            return JSONResponse(
                {"ok": False, "error": "too_many_pending_events"}, status_code=429
            )
        return JSONResponse({"ok": True})

    # -- Dispatch --

    async def _handle_command_event(self, event: CommandEvent) -> Response:
        ack_config = self._command_ack.get(event.command, True)

        # Run handlers inline for commands (need to return ack response)
        for reg in self._handlers:
            if not self._matches(reg, event):
                continue

            if ack_config is False:
                # Manual ack mode — run inline and return event.ack(...) payload if provided.
                await self._safe_call(reg.handler, event)
                if event._ack_payload is not None:
                    return JSONResponse(dict(event._ack_payload))
                return JSONResponse({"ok": True})

            # Auto-ack: spawn handler in background, return ack response
            if not self._spawn(self._safe_call(reg.handler, event)):
                return JSONResponse(
                    {
                        "response_type": "ephemeral",
                        "text": "System busy, please retry shortly.",
                    }
                )

            if isinstance(ack_config, str):
                return JSONResponse(
                    {
                        "response_type": "ephemeral",
                        "text": ack_config,
                    }
                )
            return JSONResponse(
                {
                    "response_type": "ephemeral",
                    "text": "Working...",
                }
            )

        return JSONResponse({"ok": True})

    async def _dispatch(self, event: BaseEvent) -> None:
        for reg in self._handlers:
            if self._matches(reg, event):
                await self._safe_call(reg.handler, event)

    def _matches(self, reg: _HandlerRegistration, event: BaseEvent) -> bool:
        if not isinstance(event, reg.event_type):
            return False

        if reg.platform is not None and event.platform.name != reg.platform:
            return False

        if reg.command is not None:
            if not isinstance(event, CommandEvent):
                return False
            if event.command != reg.command:
                return False

        if reg.pattern is not None:
            if not reg.pattern.search(event.text):
                return False

        if reg.emoji is not None:
            if not isinstance(event, ReactionEvent):
                return False
            if event.emoji != reg.emoji:
                return False

        if reg.raw_event_type is not None:
            if not isinstance(event, RawEvent):
                return False
            if event.event_type != reg.raw_event_type:
                return False

        return True

    async def _safe_call(self, handler: EventHandler, event: BaseEvent) -> None:
        try:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            if self._on_error is not None:
                try:
                    await self._on_error(event, exc)
                except Exception:
                    pass
            else:
                raise

    def _inject_backends(self, event: BaseEvent) -> BaseEvent:
        return replace(
            event,
            _run_backend=self._run_backend,
            _sender=self._message_sender,
        )

    def _spawn(self, coro: Any) -> bool:
        if len(self._pending_tasks) >= self._max_pending_tasks:
            if hasattr(coro, "close"):
                coro.close()
            return False
        task = asyncio.create_task(coro)
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return True

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._pending_tasks.discard(task)
        try:
            task.result()
        except Exception:
            _LOGGER.exception("bot background task failed")


class _Decorator:
    """Supports both @bot.mention and @bot.mention(pattern=...) syntax."""

    def __init__(self, bot: Bot, event_type: type[BaseEvent]) -> None:
        self._bot = bot
        self._event_type = event_type

    def __call__(
        self,
        handler: EventHandler | None = None,
        *,
        pattern: str | None = None,
        platform: Platform | None = None,
    ) -> Any:
        if handler is not None and callable(handler):
            # @bot.mention (bare decorator, no parens)
            self._bot._register(self._event_type, handler)
            return handler

        # @bot.mention(pattern=...) or @bot.message(pattern=...)
        def decorator(h: EventHandler) -> EventHandler:
            self._bot._register(self._event_type, h, pattern=pattern, platform=platform)
            return h

        return decorator


class _ReactionDecorator:
    """Supports @bot.reaction("emoji") syntax."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    def __call__(
        self,
        emoji: str,
        *,
        platform: Platform | None = None,
    ) -> Callable[[EventHandler], EventHandler]:
        def decorator(handler: EventHandler) -> EventHandler:
            self._bot._register(ReactionEvent, handler, emoji=emoji, platform=platform)
            return handler

        return decorator


def _normalize_prefix(value: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("prefix must be non-empty")
    if not trimmed.startswith("/"):
        trimmed = f"/{trimmed}"
    return trimmed.rstrip("/")


def _is_content_length_too_large(
    headers: Mapping[str, str], max_body_bytes: int
) -> bool:
    length = headers.get("content-length")
    if length is None:
        return False
    try:
        value = int(length)
    except ValueError:
        return True
    return value > max_body_bytes


class _TeamsTokenValidator:
    _BOT_FRAMEWORK_JWKS_URL = "https://login.botframework.com/v1/.well-known/keys"
    _BOT_FRAMEWORK_ISSUER = "https://api.botframework.com"

    def __init__(self, app_id: str) -> None:
        self._app_id = app_id.strip()
        if not self._app_id:
            raise ValueError("teams app_id must be non-empty")
        self._jwks_client: Any | None = None

    def validate_authorization(self, authorization: str | None) -> bool:
        if not authorization:
            return False
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return False

        try:
            from jwt import PyJWKClient, decode
            from jwt.exceptions import InvalidTokenError
        except ImportError:
            raise RuntimeError(
                "Teams auth requires pyjwt[crypto]. Install dependency 'pyjwt[crypto]>=2.10'."
            )

        if self._jwks_client is None:
            self._jwks_client = PyJWKClient(self._BOT_FRAMEWORK_JWKS_URL)

        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=[self._app_id, f"api://{self._app_id}"],
                issuer=self._BOT_FRAMEWORK_ISSUER,
                options={"require": ["exp", "iat", "nbf", "iss", "aud"]},
            )
        except InvalidTokenError:
            return False

        service_url = payload.get("serviceurl")
        if service_url is not None and (
            not isinstance(service_url, str)
            or not service_url.lower().startswith("https://")
        ):
            return False
        return True


class _NoopMessageSender:
    async def send_message(self, **kwargs: Any) -> str:
        raise LsmsgError("No message sender configured. Pass message_sender= to Bot().")

    async def update_message(self, **kwargs: Any) -> None:
        raise LsmsgError("No message sender configured.")

    async def delete_message(self, **kwargs: Any) -> None:
        raise LsmsgError("No message sender configured.")
