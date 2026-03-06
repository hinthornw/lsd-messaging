import asyncio
import time
from collections.abc import Mapping
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from lsmsg_rs import ChatApp, SlackRouteCtx


class FakeLangGraphAdapter:
    def __init__(self, assistant_id: str) -> None:
        self.assistant_id = assistant_id
        self.calls: list[dict[str, Any]] = []

    def trigger_run(
        self,
        *,
        provider: str,
        workspace_id: str,
        channel_id: str,
        root_thread_id: str,
        input: Mapping[str, Any] | None = None,
        thread_metadata: Mapping[str, Any] | None = None,
        run_metadata: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        multitask_strategy: str = "enqueue",
        if_not_exists: str = "create",
        webhook: str | None = None,
        durability: str | None = None,
    ) -> Mapping[str, Any]:
        call = {
            "provider": provider,
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "root_thread_id": root_thread_id,
            "input": dict(input) if input is not None else None,
            "thread_metadata": dict(thread_metadata) if thread_metadata is not None else None,
            "run_metadata": dict(run_metadata) if run_metadata is not None else None,
            "config": dict(config) if config is not None else None,
            "multitask_strategy": multitask_strategy,
            "if_not_exists": if_not_exists,
            "webhook": webhook,
            "durability": durability,
        }
        self.calls.append(call)
        return {
            "assistant_id": self.assistant_id,
            "thread_id": f"thread-{root_thread_id}",
            "run": {"id": f"run-{len(self.calls)}"},
        }


def fake_adapter_factory(created: dict[str, FakeLangGraphAdapter]):
    def factory(assistant_id: str) -> FakeLangGraphAdapter:
        adapter = FakeLangGraphAdapter(assistant_id=assistant_id)
        created[assistant_id] = adapter
        return adapter

    return factory


def _wait_for(condition, timeout_s: float = 1.0) -> None:  # type: ignore[no-untyped-def]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


def test_chat_app_default_routes_trigger_assistant_by_hint() -> None:
    created: dict[str, FakeLangGraphAdapter] = {}
    app = ChatApp(
        api_base_url="http://example.test",
        assistants={"default": "assistant-default", "planner": "assistant-planner"},
        default_assistant="default",
        background_dispatch=False,
        allow_unsigned_slack=True,
        adapter_factory=fake_adapter_factory(created),
    )
    client = TestClient(app.asgi_app())

    response = client.post(
        "/slack/events",
        json={
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "app_mention",
                "channel": "C1",
                "user": "U1",
                "ts": "1710000000.100",
                "text": "<@U_BOT> planner summarize this thread",
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    planner = created["assistant-planner"]
    default = created["assistant-default"]
    assert len(planner.calls) == 1
    assert len(default.calls) == 0
    assert planner.calls[0]["provider"] == "slack"
    assert planner.calls[0]["workspace_id"] == "T1"
    assert planner.calls[0]["channel_id"] == "C1"
    assert planner.calls[0]["root_thread_id"] == "1710000000.100"
    assert planner.calls[0]["input"] == {
        "messages": [{"role": "user", "content": "summarize this thread"}]
    }


def test_chat_app_command_ack_is_fast_and_dispatches_run() -> None:
    created: dict[str, FakeLangGraphAdapter] = {}
    app = ChatApp(
        api_base_url="http://example.test",
        assistants={"default": "assistant-default", "planner": "assistant-planner"},
        background_dispatch=True,
        allow_unsigned_slack=True,
        adapter_factory=fake_adapter_factory(created),
    )
    client = TestClient(app.asgi_app())

    response = client.post(
        "/slack/events",
        data={
            "command": "/agent",
            "text": "planner list open incidents",
            "team_id": "T2",
            "channel_id": "C2",
            "user_id": "U2",
            "trigger_id": "trigger-2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "Running planner...",
        "response_type": "ephemeral",
    }

    planner = created["assistant-planner"]
    _wait_for(lambda: len(planner.calls) == 1)
    assert planner.calls[0]["root_thread_id"] == "trigger-2"


def test_chat_app_command_returns_busy_ack_when_queue_full() -> None:
    created: dict[str, FakeLangGraphAdapter] = {}
    app = ChatApp(
        api_base_url="http://example.test",
        assistant_id="assistant-default",
        background_dispatch=True,
        allow_unsigned_slack=True,
        adapter_factory=fake_adapter_factory(created),
    )
    app._spawn_background = lambda _awaitable: False  # type: ignore[method-assign]
    client = TestClient(app.asgi_app())

    response = client.post(
        "/slack/events",
        data={
            "command": "/agent",
            "text": "do work",
            "team_id": "T2",
            "channel_id": "C2",
            "user_id": "U2",
            "trigger_id": "trigger-2",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "System busy, please retry shortly.",
        "response_type": "ephemeral",
    }


def test_chat_app_progressive_customizers() -> None:
    created: dict[str, FakeLangGraphAdapter] = {}
    app = ChatApp(
        api_base_url="http://example.test",
        assistants={"default": "assistant-default", "reviewer": "assistant-reviewer"},
        auto_routes=False,
        allow_unsigned_slack=True,
        adapter_factory=fake_adapter_factory(created),
    )

    @app.select_assistant
    def pick_assistant(ctx):  # type: ignore[no-untyped-def]
        if "review" in ctx.text:
            return "reviewer"
        return "default"

    @app.build_input
    def build_input(ctx, assistant):  # type: ignore[no-untyped-def]
        return {"messages": [{"role": "user", "content": f"{assistant}:{ctx.text}"}]}

    @app.build_config
    def build_config(_ctx, assistant):  # type: ignore[no-untyped-def]
        return {"configurable": {"assistant": assistant}}

    ctx = SlackRouteCtx(
        provider="slack",
        event_type="mention",
        workspace_id="T9",
        channel_id="C9",
        root_thread_id="R9",
        message_id="M9",
        user_id="U9",
        text="please review this change",
        assistant_hint=None,
        command=None,
        raw={},
        headers={},
    )
    result = asyncio.run(app.trigger(ctx))

    reviewer = created["assistant-reviewer"]
    assert len(reviewer.calls) == 1
    assert reviewer.calls[0]["input"] == {
        "messages": [{"role": "user", "content": "reviewer:please review this change"}]
    }
    assert reviewer.calls[0]["config"] == {"configurable": {"assistant": "reviewer"}}
    assert result["assistant_id"] == "assistant-reviewer"


def test_chat_app_can_register_into_existing_starlette() -> None:
    created: dict[str, FakeLangGraphAdapter] = {}
    app = ChatApp(
        api_base_url="http://example.test",
        assistant_id="assistant-default",
        background_dispatch=False,
        allow_unauthenticated_teams=True,
        adapter_factory=fake_adapter_factory(created),
    )
    starlette_app = Starlette()
    app.register_routes(starlette_app, prefix="/hooks")
    client = TestClient(starlette_app)

    response = client.post(
        "/hooks/teams/events",
        json={
            "type": "message",
            "id": "teams-1",
            "text": "<at>bot</at> hello there",
            "from": {"id": "U7"},
            "conversation": {"id": "CONV7"},
            "channelData": {"tenant": {"id": "TEN7"}, "team": {"id": "TEAM7"}},
            "entities": [{"type": "mention", "text": "<at>bot</at>"}],
        },
    )

    assert response.status_code == 200
    assert created["assistant-default"].calls[0]["provider"] == "teams"


def test_chat_app_from_env_requires_api_url(monkeypatch) -> None:
    monkeypatch.delenv("LANGGRAPH_API_URL", raising=False)
    with pytest.raises(ValueError, match="LANGGRAPH_API_URL"):
        ChatApp.from_env(assistant_id="assistant-default")


def test_chat_app_validates_default_assistant() -> None:
    with pytest.raises(ValueError, match="default_assistant"):
        ChatApp(
            api_base_url="http://example.test",
            assistants={"planner": "assistant-planner"},
            default_assistant="missing",
            allow_unsigned_slack=True,
            adapter_factory=fake_adapter_factory({}),
        )
