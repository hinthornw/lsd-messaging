from __future__ import annotations

import re
from typing import Any

from ._capabilities import TEAMS_CAPABILITIES
from ._events import (
    BaseEvent,
    MentionEvent,
    MessageEvent,
    ReactionEvent,
    UserInfo,
)


def parse_teams_webhook(payload: dict[str, Any]) -> BaseEvent | None:
    activity_type = str(payload.get("type") or "")

    if activity_type == "messageReaction":
        return _parse_reaction(payload)

    if activity_type != "message":
        return None

    from_user = payload.get("from") or {}
    conversation = payload.get("conversation") or {}
    channel_data = payload.get("channelData") or {}
    tenant = channel_data.get("tenant") or {}
    team = channel_data.get("team") or {}

    raw_text = str(payload.get("text") or "")
    text = _strip_teams_mentions(raw_text)

    entities = payload.get("entities")
    has_mention = isinstance(entities, list) and any(
        isinstance(e, dict) and e.get("type") == "mention" for e in entities
    )
    is_mention = has_mention or text != raw_text

    conversation_id = str(conversation.get("id") or "unknown")
    root_id = str(
        payload.get("replyToId") or conversation_id or payload.get("id") or "unknown"
    )
    message_id = str(payload.get("id") or root_id)
    workspace_id = str(tenant.get("id") or conversation.get("tenantId") or "unknown")
    channel_id = str(team.get("id") or conversation_id)
    user_id = str(from_user.get("id") or "unknown")
    user_name = from_user.get("name")

    user = UserInfo(id=user_id, name=user_name)
    cls = MentionEvent if is_mention else MessageEvent
    return cls(
        platform=TEAMS_CAPABILITIES,
        workspace_id=workspace_id,
        channel_id=channel_id,
        thread_id=root_id,
        message_id=message_id,
        user=user,
        text=text,
        raw=payload,
    )


def _parse_reaction(payload: dict[str, Any]) -> ReactionEvent | None:
    from_user = payload.get("from") or {}
    conversation = payload.get("conversation") or {}
    channel_data = payload.get("channelData") or {}
    tenant = channel_data.get("tenant") or {}
    team = channel_data.get("team") or {}

    reactions_added = payload.get("reactionsAdded") or []
    if not reactions_added:
        return None
    emoji = str(reactions_added[0].get("type") or "")

    conversation_id = str(conversation.get("id") or "unknown")
    root_id = str(payload.get("replyToId") or conversation_id or "unknown")

    return ReactionEvent(
        platform=TEAMS_CAPABILITIES,
        workspace_id=str(tenant.get("id") or "unknown"),
        channel_id=str(team.get("id") or conversation_id),
        thread_id=root_id,
        message_id=str(payload.get("id") or root_id),
        user=UserInfo(
            id=str(from_user.get("id") or "unknown"), name=from_user.get("name")
        ),
        text="",
        emoji=emoji,
        raw=payload,
    )


def _strip_teams_mentions(text: str) -> str:
    without_tags = re.sub(r"<at>.*?</at>", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", without_tags).strip()
