from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Mapping
from urllib.parse import parse_qs

from ._capabilities import SLACK_CAPABILITIES
from ._events import (
    BaseEvent,
    CommandEvent,
    MentionEvent,
    MessageEvent,
    ReactionEvent,
    UserInfo,
)


def verify_slack_signature(
    *,
    signing_secret: str,
    headers: Mapping[str, str],
    body: bytes,
) -> bool:
    timestamp = _header(headers, "x-slack-request-timestamp")
    signature = _header(headers, "x-slack-signature")
    if not timestamp or not signature:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - ts_int) > 60 * 5:
        return False
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"
    return hmac.compare_digest(expected, signature)


def parse_slack_webhook(
    body: bytes, headers: Mapping[str, str]
) -> BaseEvent | dict[str, Any] | None:
    """Parse a Slack webhook into an event.

    Returns:
        - A typed event for dispatching
        - A dict for url_verification challenges ({"challenge": ...})
        - None for events we should ignore
    """
    content_type = _header(headers, "content-type") or ""
    if content_type.startswith("application/x-www-form-urlencoded"):
        return _parse_form(body)

    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return None

    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    return _parse_event_callback(payload)


def _parse_form(body: bytes) -> BaseEvent | None:
    decoded = body.decode("utf-8")
    form = parse_qs(decoded, keep_blank_values=True)

    if "payload" in form:
        raw_payload = _first(form.get("payload"), "{}")
        try:
            payload: dict[str, Any] = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
        return _parse_interaction(payload)

    command = _first(form.get("command"), "").strip()
    if not command:
        return None

    text = _first(form.get("text"), "")
    workspace_id = _first(form.get("team_id"), "unknown")
    channel_id = _first(form.get("channel_id"), "unknown")
    trigger_id = _first(form.get("trigger_id"), "")
    thread_ts = _first(form.get("thread_ts"), "")
    root_id = thread_ts or trigger_id or f"slash-{int(time.time() * 1000)}"
    message_id = trigger_id or root_id
    user_id = _first(form.get("user_id"), "unknown")
    user_name = _first(form.get("user_name"), None)

    return CommandEvent(
        platform=SLACK_CAPABILITIES,
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_id=root_id,
        message_id=message_id,
        user=UserInfo(id=user_id, name=user_name),
        text=text,
        command=command,
        raw={k: v[0] if len(v) == 1 else v for k, v in form.items()},
    )


def _parse_interaction(payload: dict[str, Any]) -> BaseEvent | None:
    interaction_type = str(payload.get("type") or "")
    if interaction_type not in {
        "block_actions",
        "shortcut",
        "message_action",
        "view_submission",
    }:
        return None

    user = payload.get("user") or {}
    channel = payload.get("channel") or {}
    team = payload.get("team") or {}
    message = payload.get("message") or {}

    text = str(message.get("text") or "")
    root_id = str(
        message.get("thread_ts")
        or message.get("ts")
        or payload.get("trigger_id")
        or f"interaction-{int(time.time() * 1000)}"
    )

    return MessageEvent(
        platform=SLACK_CAPABILITIES,
        workspace_id=str(team.get("id") or "unknown"),
        channel_id=str(channel.get("id") or "unknown"),
        thread_id=root_id,
        message_id=str(message.get("client_msg_id") or message.get("ts") or root_id),
        user=UserInfo(id=str(user.get("id") or "unknown"), name=user.get("username")),
        text=text,
        raw=payload,
    )


def _parse_event_callback(payload: dict[str, Any]) -> BaseEvent | None:
    if payload.get("type") != "event_callback":
        return None

    event = payload.get("event")
    if not isinstance(event, dict):
        return None

    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return None

    event_type_raw = str(event.get("type") or "")
    raw_text = str(event.get("text") or "")
    text = _strip_slack_mentions(raw_text)

    is_mention = event_type_raw == "app_mention" or bool(
        re.search(r"<@[^>]+>", raw_text)
    )

    workspace_id = str(payload.get("team_id") or event.get("team") or "unknown")
    channel_id = str(event.get("channel") or "unknown")
    root_id = str(
        event.get("thread_ts") or event.get("ts") or f"event-{int(time.time() * 1000)}"
    )
    message_id = str(event.get("client_msg_id") or event.get("ts") or root_id)
    user_id = str(event.get("user") or "unknown")

    if event_type_raw == "reaction_added":
        return ReactionEvent(
            platform=SLACK_CAPABILITIES,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_id=root_id,
            message_id=message_id,
            user=UserInfo(id=user_id),
            text=text,
            emoji=str(event.get("reaction") or ""),
            raw=payload,
        )

    if is_mention:
        return MentionEvent(
            platform=SLACK_CAPABILITIES,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_id=root_id,
            message_id=message_id,
            user=UserInfo(id=user_id),
            text=text,
            raw=payload,
        )

    return MessageEvent(
        platform=SLACK_CAPABILITIES,
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_id=root_id,
        message_id=message_id,
        user=UserInfo(id=user_id),
        text=text,
        raw=payload,
    )


def _strip_slack_mentions(text: str) -> str:
    cleaned = re.sub(r"<@[^>]+>\s*", "", text).strip()
    return cleaned


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return headers.get(name) or headers.get(name.title()) or headers.get(name.upper())


def _first(values: list[str] | None, default: str | None) -> str:
    if not values:
        return default or ""
    return values[0]
