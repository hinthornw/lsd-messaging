import hashlib
import hmac
import json
import time

from starlette.applications import Starlette
from starlette.testclient import TestClient

from lsmsg_rs import ChatBridge, SlackAck, SlackRouteCtx, TeamsRouteCtx


def _slack_signature(secret: str, body: bytes, timestamp: int) -> str:
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_asgi_app_dispatches_slack_mention_ctx() -> None:
    bridge = ChatBridge(background_dispatch=False, allow_unsigned_slack=True)
    seen: list[SlackRouteCtx] = []

    @bridge.on_mention(provider="slack")
    async def handler(ctx):  # type: ignore[no-untyped-def]
        assert isinstance(ctx, SlackRouteCtx)
        seen.append(ctx)

    app = bridge.asgi_app()
    client = TestClient(app)

    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "ts": "1710000000.100",
            "text": "<@U_BOT> planner run status",
        },
    }
    response = client.post("/slack/events", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(seen) == 1

    ctx = seen[0]
    assert ctx.provider == "slack"
    assert ctx.event_type == "mention"
    assert ctx.workspace_id == "T123"
    assert ctx.channel_id == "C123"
    assert ctx.root_thread_id == "1710000000.100"
    assert ctx.message_id == "1710000000.100"
    assert ctx.user_id == "U123"
    assert ctx.assistant_hint == "planner"
    assert ctx.thread_key == ("slack", "T123", "C123", "1710000000.100")


def test_on_command_matches_slash_command() -> None:
    bridge = ChatBridge(background_dispatch=False, allow_unsigned_slack=True)
    seen: list[SlackRouteCtx] = []

    @bridge.on_command("/agent")
    async def handler(ctx):  # type: ignore[no-untyped-def]
        assert isinstance(ctx, SlackRouteCtx)
        seen.append(ctx)

    client = TestClient(bridge.asgi_app())
    response = client.post(
        "/slack/events",
        data={
            "command": "/agent",
            "text": "planner summarize thread",
            "team_id": "T1",
            "channel_id": "C1",
            "user_id": "U1",
            "trigger_id": "1337.99",
        },
    )

    assert response.status_code == 200
    assert len(seen) == 1
    ctx = seen[0]
    assert ctx.event_type == "command"
    assert ctx.command == "/agent"
    assert ctx.assistant_hint == "planner"
    assert ctx.thread_key == ("slack", "T1", "C1", "1337.99")


def test_on_command_can_return_custom_slack_ack_payload() -> None:
    bridge = ChatBridge(background_dispatch=True, allow_unsigned_slack=True)

    @bridge.on_command("/agent")
    async def handler(_ctx):  # type: ignore[no-untyped-def]
        return SlackAck(text="Running now", response_type="ephemeral")

    client = TestClient(bridge.asgi_app())
    response = client.post(
        "/slack/events",
        data={
            "command": "/agent",
            "text": "planner summarize thread",
            "team_id": "T1",
            "channel_id": "C1",
            "user_id": "U1",
            "trigger_id": "1337.99",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"text": "Running now", "response_type": "ephemeral"}


def test_slack_signature_verification() -> None:
    secret = "signing-secret"
    bridge = ChatBridge(slack_signing_secret=secret, background_dispatch=False)
    seen: list[str] = []

    @bridge.on_event(provider="slack")
    async def handler(ctx):  # type: ignore[no-untyped-def]
        seen.append(ctx.message_id)

    client = TestClient(bridge.asgi_app())
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "ts": "1710000000.100",
            "text": "<@U_BOT> planner run status",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = _slack_signature(secret, body, timestamp)

    ok_response = client.post(
        "/slack/events",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": str(timestamp),
            "x-slack-signature": signature,
        },
    )
    assert ok_response.status_code == 200
    assert seen == ["1710000000.100"]

    bad_response = client.post(
        "/slack/events",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": str(timestamp),
            "x-slack-signature": "v0=not-valid",
        },
    )
    assert bad_response.status_code == 401
    assert seen == ["1710000000.100"]

    stale_timestamp = timestamp - 600
    stale_signature = _slack_signature(secret, body, stale_timestamp)
    stale_response = client.post(
        "/slack/events",
        content=body,
        headers={
            "content-type": "application/json",
            "x-slack-request-timestamp": str(stale_timestamp),
            "x-slack-signature": stale_signature,
        },
    )
    assert stale_response.status_code == 401


def test_register_routes_on_existing_starlette_app_with_teams() -> None:
    bridge = ChatBridge(background_dispatch=False, allow_unauthenticated_teams=True)
    seen: list[TeamsRouteCtx] = []

    @bridge.on_mention(provider="teams")
    async def handler(ctx):  # type: ignore[no-untyped-def]
        assert isinstance(ctx, TeamsRouteCtx)
        seen.append(ctx)

    app = Starlette()
    bridge.register_routes(app, prefix="/hooks")
    client = TestClient(app)

    payload = {
        "type": "message",
        "id": "m1",
        "text": "<at>bot</at> planner summarize",
        "from": {"id": "user-1"},
        "conversation": {"id": "conv-1"},
        "channelData": {"tenant": {"id": "tenant-1"}, "team": {"id": "team-1"}},
        "entities": [{"type": "mention", "text": "<at>bot</at>"}],
    }
    response = client.post("/hooks/teams/events", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(seen) == 1
    ctx = seen[0]
    assert ctx.provider == "teams"
    assert ctx.event_type == "mention"
    assert ctx.workspace_id == "tenant-1"
    assert ctx.channel_id == "team-1"
    assert ctx.root_thread_id == "conv-1"
    assert ctx.user_id == "user-1"
    assert ctx.text == "planner summarize"
    assert ctx.assistant_hint == "planner"
    assert ctx.thread_key == ("teams", "tenant-1", "team-1", "conv-1")


def test_mount_uses_bridge_sub_app() -> None:
    bridge = ChatBridge(background_dispatch=False, allow_unsigned_slack=True)
    seen: list[str] = []

    @bridge.on_message(provider="slack")
    async def handler(ctx):  # type: ignore[no-untyped-def]
        seen.append(ctx.message_id)

    app = Starlette()
    bridge.mount(app, path="/chat")
    client = TestClient(app)

    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event": {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "ts": "1710000000.200",
            "text": "plain message",
        },
    }
    response = client.post("/chat/slack/events", json=payload)

    assert response.status_code == 200
    assert seen == ["1710000000.200"]


def test_slack_requires_signing_secret_by_default() -> None:
    bridge = ChatBridge(background_dispatch=False)
    client = TestClient(bridge.asgi_app())
    payload = {
        "type": "event_callback",
        "team_id": "T1",
        "event": {"type": "message", "channel": "C1", "user": "U1", "ts": "1", "text": "x"},
    }
    response = client.post("/slack/events", json=payload)
    assert response.status_code == 503


def test_teams_requires_auth_by_default() -> None:
    bridge = ChatBridge(background_dispatch=False)
    client = TestClient(bridge.asgi_app())
    payload = {
        "type": "message",
        "id": "m1",
        "text": "hello",
        "from": {"id": "user-1"},
        "conversation": {"id": "conv-1"},
    }
    response = client.post("/teams/events", json=payload)
    assert response.status_code == 503


def test_teams_with_app_id_requires_bearer_token() -> None:
    bridge = ChatBridge(background_dispatch=False, teams_app_id="app-123")
    client = TestClient(bridge.asgi_app())
    payload = {
        "type": "message",
        "id": "m1",
        "text": "hello",
        "from": {"id": "user-1"},
        "conversation": {"id": "conv-1"},
    }
    response = client.post("/teams/events", json=payload)
    assert response.status_code == 401


def test_payload_too_large_returns_413() -> None:
    bridge = ChatBridge(
        background_dispatch=False,
        allow_unsigned_slack=True,
        max_body_bytes=16,
    )
    client = TestClient(bridge.asgi_app())
    response = client.post(
        "/slack/events",
        content=b'{"type":"event_callback","event":{"type":"message"}}',
        headers={"content-type": "application/json", "content-length": "1024"},
    )
    assert response.status_code == 413


def test_invalid_utf8_form_returns_400() -> None:
    bridge = ChatBridge(background_dispatch=False, allow_unsigned_slack=True)
    client = TestClient(bridge.asgi_app())
    response = client.post(
        "/slack/events",
        content=b"\xff\xfe\xfd",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
