"""Test utilities for lsmsg bots.

Provides BotTestClient for integration testing without real Slack/Teams.
Simulates webhook payloads, captures replies, and asserts on bot behavior.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from starlette.testclient import TestClient

from ._capabilities import Platform


@dataclass(slots=True)
class BotTestResult:
    """Captures what the bot did in response to a simulated event."""

    status_code: int
    response_json: dict[str, Any]
    # Populated by the fake sender
    replies: list[dict[str, Any]] = field(default_factory=list)
    whispers: list[dict[str, Any]] = field(default_factory=list)
    updates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ack_text(self) -> str | None:
        return self.response_json.get("text")

    @property
    def ack_type(self) -> str | None:
        return self.response_json.get("response_type")

    def replied_with(self, text: str) -> bool:
        return any(r.get("text") == text for r in self.replies)

    @property
    def reply_count(self) -> int:
        return len(self.replies)


class BotTestClient:
    """Simulates webhook events against a Bot for testing.

    Usage:
        from lsmsg.testing import BotTestClient

        client = BotTestClient(bot)
        result = client.mention("hello bot")
        assert result.status_code == 200
    """

    def __init__(self, bot: Any, *, signing_secret: str = "test-secret") -> None:
        self._bot = bot
        self._signing_secret = signing_secret
        self._client = TestClient(bot)

    def mention(
        self,
        text: str,
        *,
        platform: Platform = "slack",
        user_id: str = "U-test",
        channel_id: str = "C-test",
        workspace_id: str = "T-test",
        thread_ts: str | None = None,
    ) -> BotTestResult:
        if platform == "slack":
            return self._slack_event(
                event_type="app_mention",
                text=f"<@UBOT> {text}",
                user_id=user_id,
                channel_id=channel_id,
                workspace_id=workspace_id,
                thread_ts=thread_ts,
            )
        elif platform == "teams":
            return self._teams_event(
                text=f"<at>Bot</at> {text}",
                user_id=user_id,
                channel_id=channel_id,
                workspace_id=workspace_id,
                is_mention=True,
            )
        raise ValueError(f"Unsupported platform for test: {platform}")

    def message(
        self,
        text: str,
        *,
        platform: Platform = "slack",
        user_id: str = "U-test",
        channel_id: str = "C-test",
        workspace_id: str = "T-test",
    ) -> BotTestResult:
        if platform == "slack":
            return self._slack_event(
                event_type="message",
                text=text,
                user_id=user_id,
                channel_id=channel_id,
                workspace_id=workspace_id,
            )
        elif platform == "teams":
            return self._teams_event(
                text=text,
                user_id=user_id,
                channel_id=channel_id,
                workspace_id=workspace_id,
            )
        raise ValueError(f"Unsupported platform for test: {platform}")

    def command(
        self,
        name: str,
        text: str = "",
        *,
        user_id: str = "U-test",
        channel_id: str = "C-test",
        workspace_id: str = "T-test",
    ) -> BotTestResult:
        form_data = urlencode(
            {
                "command": name,
                "text": text,
                "team_id": workspace_id,
                "channel_id": channel_id,
                "user_id": user_id,
                "user_name": "testuser",
                "trigger_id": f"trig-{int(time.time() * 1000)}",
            }
        ).encode()
        headers = self._sign(form_data)
        headers["content-type"] = "application/x-www-form-urlencoded"
        resp = self._client.post("/slack/events", content=form_data, headers=headers)
        return BotTestResult(
            status_code=resp.status_code,
            response_json=resp.json() if resp.status_code == 200 else {},
        )

    def reaction(
        self,
        emoji: str,
        *,
        platform: Platform = "slack",
        user_id: str = "U-test",
        channel_id: str = "C-test",
        workspace_id: str = "T-test",
    ) -> BotTestResult:
        if platform == "slack":
            return self._slack_event(
                event_type="reaction_added",
                text="",
                user_id=user_id,
                channel_id=channel_id,
                workspace_id=workspace_id,
                extra_event={"reaction": emoji},
            )
        raise ValueError(f"Unsupported platform for reaction test: {platform}")

    def _slack_event(
        self,
        *,
        event_type: str,
        text: str,
        user_id: str,
        channel_id: str,
        workspace_id: str,
        thread_ts: str | None = None,
        extra_event: dict[str, Any] | None = None,
    ) -> BotTestResult:
        ts = f"{int(time.time())}.{int(time.time() * 1000) % 1000:06d}"
        event: dict[str, Any] = {
            "type": event_type,
            "text": text,
            "channel": channel_id,
            "ts": ts,
            "user": user_id,
        }
        if thread_ts:
            event["thread_ts"] = thread_ts
        if extra_event:
            event.update(extra_event)

        payload = {
            "type": "event_callback",
            "team_id": workspace_id,
            "event": event,
        }
        body = json.dumps(payload).encode()
        headers = self._sign(body)
        headers["content-type"] = "application/json"
        resp = self._client.post("/slack/events", content=body, headers=headers)
        return BotTestResult(
            status_code=resp.status_code,
            response_json=resp.json() if resp.status_code == 200 else {},
        )

    def _teams_event(
        self,
        *,
        text: str,
        user_id: str,
        channel_id: str,
        workspace_id: str,
        is_mention: bool = False,
    ) -> BotTestResult:
        payload: dict[str, Any] = {
            "type": "message",
            "text": text,
            "from": {"id": user_id, "name": "Test User"},
            "conversation": {"id": f"conv-{channel_id}", "tenantId": workspace_id},
            "channelData": {
                "tenant": {"id": workspace_id},
                "team": {"id": channel_id},
            },
            "id": f"msg-{int(time.time() * 1000)}",
        }
        if is_mention:
            payload["entities"] = [{"type": "mention", "mentioned": {"id": "bot-1"}}]

        body = json.dumps(payload).encode()
        resp = self._client.post(
            "/teams/events",
            content=body,
            headers={"content-type": "application/json"},
        )
        return BotTestResult(
            status_code=resp.status_code,
            response_json=resp.json() if resp.status_code == 200 else {},
        )

    def _sign(self, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        base = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
        digest = hmac.new(
            self._signing_secret.encode("utf-8"), base, hashlib.sha256
        ).hexdigest()
        return {
            "x-slack-request-timestamp": ts,
            "x-slack-signature": f"v0={digest}",
        }
