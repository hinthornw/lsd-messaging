"""Testing utilities for lsmsg bots."""

from __future__ import annotations

import json
from typing import Any, Optional

from lsmsg._bot import Bot


class BotTestClient:
    """A test client that simulates webhook requests to a Bot without real HTTP.

    Requires ``httpx`` (install with ``pip install lsmsg[dev]``).

    Example::

        from lsmsg.testing import BotTestClient

        client = BotTestClient(bot)
        resp = await client.send_slack_mention("hello", user_id="U1", channel_id="C1")
        assert resp.status_code == 200
    """

    def __init__(self, bot: Bot, prefix: str = "") -> None:
        from httpx import ASGITransport, AsyncClient

        self.bot = bot
        self.prefix = prefix.rstrip("/")
        self._transport = ASGITransport(app=bot)  # type: ignore[arg-type]
        self._client = AsyncClient(
            transport=self._transport, base_url="http://testserver"
        )

    async def send_slack_event(
        self,
        payload: dict[str, Any],
        content_type: str = "application/json",
    ) -> Any:
        """Send a raw Slack event payload."""
        if content_type == "application/json":
            body = json.dumps(payload).encode()
        else:
            body = payload.get("_raw_body", b"")  # type: ignore[assignment]
        return await self._client.post(
            f"{self.prefix}/slack/events",
            content=body,
            headers={"content-type": content_type},
        )

    async def send_slack_mention(
        self,
        text: str,
        *,
        user_id: str = "U_TEST",
        channel_id: str = "C_TEST",
        team_id: str = "T_TEST",
        thread_ts: Optional[str] = None,
    ) -> Any:
        """Simulate a Slack app_mention event."""
        ts = thread_ts or "1234567890.123456"
        payload = {
            "type": "event_callback",
            "team_id": team_id,
            "event": {
                "type": "app_mention",
                "text": f"<@UBOT> {text}",
                "channel": channel_id,
                "ts": ts,
                "user": user_id,
            },
        }
        return await self.send_slack_event(payload)

    async def send_slack_message(
        self,
        text: str,
        *,
        user_id: str = "U_TEST",
        channel_id: str = "C_TEST",
        team_id: str = "T_TEST",
        thread_ts: Optional[str] = None,
    ) -> Any:
        """Simulate a Slack message event."""
        ts = thread_ts or "1234567890.123456"
        payload = {
            "type": "event_callback",
            "team_id": team_id,
            "event": {
                "type": "message",
                "text": text,
                "channel": channel_id,
                "ts": ts,
                "user": user_id,
            },
        }
        return await self.send_slack_event(payload)

    async def send_slack_command(
        self,
        command: str,
        text: str = "",
        *,
        user_id: str = "U_TEST",
        channel_id: str = "C_TEST",
        team_id: str = "T_TEST",
        trigger_id: str = "trig_test",
    ) -> Any:
        """Simulate a Slack slash command."""
        from urllib.parse import urlencode

        form_data = urlencode(
            {
                "command": command,
                "text": text,
                "team_id": team_id,
                "channel_id": channel_id,
                "user_id": user_id,
                "trigger_id": trigger_id,
            }
        )
        return await self._client.post(
            f"{self.prefix}/slack/events",
            content=form_data.encode(),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    async def send_slack_reaction(
        self,
        emoji: str,
        *,
        user_id: str = "U_TEST",
        channel_id: str = "C_TEST",
        team_id: str = "T_TEST",
    ) -> Any:
        """Simulate a Slack reaction_added event."""
        payload = {
            "type": "event_callback",
            "team_id": team_id,
            "event": {
                "type": "reaction_added",
                "reaction": emoji,
                "channel": channel_id,
                "ts": "1234567890.123456",
                "user": user_id,
            },
        }
        return await self.send_slack_event(payload)

    async def send_teams_event(self, payload: dict[str, Any]) -> Any:
        """Send a raw Teams activity payload."""
        return await self._client.post(
            f"{self.prefix}/teams/events",
            json=payload,
        )

    async def send_teams_message(
        self,
        text: str,
        *,
        user_id: str = "U_TEST",
        user_name: str = "Test User",
        conversation_id: str = "conv_test",
        tenant_id: str = "tenant_test",
        team_id: str = "team_test",
    ) -> Any:
        """Simulate a Teams message activity."""
        payload = {
            "type": "message",
            "text": text,
            "from": {"id": user_id, "name": user_name},
            "conversation": {"id": conversation_id, "tenantId": tenant_id},
            "channelData": {
                "tenant": {"id": tenant_id},
                "team": {"id": team_id},
            },
            "id": "msg_test_1",
        }
        return await self.send_teams_event(payload)

    async def send_teams_mention(
        self,
        text: str,
        *,
        user_id: str = "U_TEST",
        user_name: str = "Test User",
        conversation_id: str = "conv_test",
        tenant_id: str = "tenant_test",
        team_id: str = "team_test",
    ) -> Any:
        """Simulate a Teams mention activity."""
        payload = {
            "type": "message",
            "text": f"<at>Bot</at> {text}",
            "from": {"id": user_id, "name": user_name},
            "conversation": {"id": conversation_id, "tenantId": tenant_id},
            "channelData": {
                "tenant": {"id": tenant_id},
                "team": {"id": team_id},
            },
            "id": "msg_test_1",
            "entities": [{"type": "mention", "mentioned": {"id": "bot_id"}}],
        }
        return await self.send_teams_event(payload)

    async def aclose(self) -> None:
        await self._client.aclose()
