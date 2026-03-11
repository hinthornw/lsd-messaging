"""Platform adapters for Slack, Teams, etc."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import time

import orjson


from typing import Any, Callable, Coroutine, Optional, Protocol, runtime_checkable
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from lsmsg._types import Event, SentMessage

logger = logging.getLogger("lsmsg")

# The dispatch callback type — set by Bot when building routes.
DispatchFn = Callable[
    [Event, "Adapter", Optional[list[int]]], Coroutine[Any, Any, None]
]


@runtime_checkable
class Adapter(Protocol):
    """Protocol that all platform adapters implement."""

    name: str

    def routes(self, dispatch: DispatchFn) -> list[Route]: ...

    async def send_message(
        self, *, channel_id: str, thread_id: str, text: str
    ) -> SentMessage: ...

    async def send_ephemeral(
        self, *, channel_id: str, thread_id: str, user_id: str, text: str
    ) -> SentMessage: ...


class Slack:
    """Slack platform adapter.

    Args:
        signing_secret: Slack app signing secret for verifying webhooks.
        bot_token: Slack bot OAuth token for sending messages.
        name: Unique name for this adapter instance. Used as the route prefix.
              Defaults to ``"slack"``.
    """

    def __init__(
        self,
        *,
        signing_secret: str,
        bot_token: str = "",
        name: str = "slack",
    ) -> None:
        self.signing_secret = signing_secret
        self.bot_token = bot_token
        self.name = name

    def routes(self, dispatch: DispatchFn) -> list[Route]:
        async def handle(request: Request) -> Response:
            return await self._handle_webhook(request, dispatch)

        return [
            Route(f"/{self.name}/events", handle, methods=["POST"]),
        ]

    async def _handle_webhook(self, request: Request, dispatch: DispatchFn) -> Response:
        body = await request.body()
        content_type = request.headers.get("content-type", "application/json")

        # Signature verification
        if self.signing_secret:
            timestamp = request.headers.get("x-slack-request-timestamp", "")
            signature = request.headers.get("x-slack-signature", "")
            if not timestamp or not signature:
                return JSONResponse(
                    {"error": "missing signature headers"}, status_code=401
                )
            valid = await asyncio.to_thread(
                _verify_slack_signature,
                self.signing_secret,
                timestamp,
                signature,
                body,
            )
            if not valid:
                return JSONResponse({"error": "invalid signature"}, status_code=401)

        result = await asyncio.to_thread(_parse_slack_webhook, body, content_type)

        result_type = result.get("type")

        if result_type == "rejected":
            return JSONResponse(
                {"error": result.get("error", "request rejected")},
                status_code=result.get("status_code", 400),
            )

        if result_type == "challenge":
            return JSONResponse({"challenge": result["challenge"]})

        if result_type == "ignored":
            return JSONResponse({"ok": True})

        if result_type in {"event", "dispatch"}:
            event = Event.from_dict(result["event"])
            await dispatch(event, self, None)
            return JSONResponse({"ok": True})

        return JSONResponse({"ok": True})

    async def send_message(
        self, *, channel_id: str, thread_id: str, text: str
    ) -> SentMessage:
        if not self.bot_token:
            raise RuntimeError(
                f"Slack adapter '{self.name}' has no bot_token configured"
            )

        import httpx

        payload = {
            "channel": channel_id,
            "text": text,
            "thread_ts": thread_id,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            return SentMessage(
                id=data.get("ts", ""),
                platform="slack",
                channel_id=channel_id,
            )

    async def send_ephemeral(
        self, *, channel_id: str, thread_id: str, user_id: str, text: str
    ) -> SentMessage:
        if not self.bot_token:
            raise RuntimeError(
                f"Slack adapter '{self.name}' has no bot_token configured"
            )

        import httpx

        payload = {
            "channel": channel_id,
            "text": text,
            "thread_ts": thread_id,
            "user": user_id,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postEphemeral",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack API error: {data.get('error', 'unknown')}")
            return SentMessage(
                id=data.get("message_ts", ""),
                platform="slack",
                channel_id=channel_id,
            )


class Teams:
    """Microsoft Teams platform adapter.

    Args:
        app_id: Teams bot app ID.
        app_password: Teams bot app password.
        name: Unique name for this adapter instance. Defaults to ``"teams"``.
    """

    def __init__(
        self,
        *,
        app_id: str = "",
        app_password: str = "",
        name: str = "teams",
    ) -> None:
        self.app_id = app_id
        self.app_password = app_password
        self.name = name

    def routes(self, dispatch: DispatchFn) -> list[Route]:
        async def handle(request: Request) -> Response:
            return await self._handle_webhook(request, dispatch)

        return [
            Route(f"/{self.name}/events", handle, methods=["POST"]),
        ]

    async def _handle_webhook(self, request: Request, dispatch: DispatchFn) -> Response:
        body = await request.body()

        try:
            payload = _json_loads(body)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid json"}, status_code=400)
        result = await asyncio.to_thread(_parse_teams_webhook, payload)

        if result is None or result.get("type") == "ignored":
            return JSONResponse({"ok": True})

        if result.get("type") == "rejected":
            return JSONResponse(
                {"error": result.get("error", "request rejected")},
                status_code=result.get("status_code", 400),
            )

        event_payload = result["event"] if result.get("type") == "dispatch" else result
        event = Event.from_dict(event_payload)
        await dispatch(event, self, None)
        return JSONResponse({"ok": True})

    async def send_message(
        self, *, channel_id: str, thread_id: str, text: str
    ) -> SentMessage:
        # Teams reply requires the service URL from the original activity.
        # Full implementation would use Bot Framework REST API.
        logger.warning("Teams send_message not yet fully implemented")
        return SentMessage(id="pending", platform="teams", channel_id=channel_id)

    async def send_ephemeral(
        self, *, channel_id: str, thread_id: str, user_id: str, text: str
    ) -> SentMessage:
        logger.warning("Teams send_ephemeral not supported")
        return SentMessage(id="pending", platform="teams", channel_id=channel_id)


# ---------------------------------------------------------------------------
# Pure-Python webhook parsing (used when native extension is unavailable)
# ---------------------------------------------------------------------------


def _verify_slack_signature(
    signing_secret: str, timestamp: str, signature: str, body: bytes
) -> bool:
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    now = int(time.time())
    if abs(now - ts) > 60 * 5:
        return False
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def _parse_slack_webhook(body: bytes, content_type: str) -> dict[str, Any]:
    if content_type.startswith("application/x-www-form-urlencoded"):
        decoded = body.decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(decoded).items()}

        if "payload" in form:
            payload = _json_loads(form["payload"])
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
                    "platform": _slack_caps(),
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
                "platform": _slack_caps(),
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

    try:
        payload = _json_loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"type": "ignored"}

    if payload.get("type") == "url_verification":
        return {"type": "challenge", "challenge": payload.get("challenge", "")}

    if payload.get("type") != "event_callback":
        return {"type": "ignored"}

    event = payload.get("event", {})
    if not event:
        return {"type": "ignored"}

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
        "platform": _slack_caps(),
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


def _parse_teams_webhook(
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
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
            "type": "dispatch",
            "event": {
                "kind": "reaction",
                "platform": _teams_caps(),
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
            },
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
        "type": "dispatch",
        "event": {
            "kind": kind,
            "platform": _teams_caps(),
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
        },
    }


def _slack_caps() -> dict[str, Any]:
    return {
        "name": "slack",
        "ephemeral": True,
        "threads": True,
        "reactions": True,
        "streaming": True,
        "modals": True,
        "typing_indicator": True,
    }


def _teams_caps() -> dict[str, Any]:
    return {
        "name": "teams",
        "ephemeral": False,
        "threads": True,
        "reactions": False,
        "streaming": False,
        "modals": False,
        "typing_indicator": True,
    }


def _json_loads(data: bytes | str) -> Any:
    return orjson.loads(data)
