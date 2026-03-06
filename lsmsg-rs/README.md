# lsmsg-rs

`lsmsg-rs` is a Rust-first unified chat runtime with native Python bindings built via `pyo3` + `maturin`.

## What this repo contains

- High-performance core runtime in Rust (`Chat`, `Thread`, `Channel`, `Message`, in-memory adapter/state)
- Python module exposed as `lsmsg_rs`
- Native wheel build config (manylinux/macOS/Windows ready)
- API-level tests for all user-facing classes and methods

## Build and install (local dev)

```bash
cd lsmsg-rs
python -m venv .venv
source .venv/bin/activate
pip install -U pip maturin pytest
maturin develop
```

## Run tests

```bash
# Rust tests
cargo test

# Python API tests
pytest tests/python/test_api.py tests/python/test_bridge.py tests/python/test_chat_app.py
```

## Basic usage

```python
from lsmsg_rs import Chat, InMemoryAdapter, Author, Message

chat = Chat(user_name="bot")
adapter = InMemoryAdapter(name="slack", user_name="bot", bot_user_id="U_BOT")
chat.add_adapter(adapter)

chat.on_new_mention(lambda thread, message: thread.post(f"Saw mention: {message.text}"))

author = Author(user_id="U1", user_name="alice")
message = Message(id="m1", thread_id="slack:C123:T456", text="hi @bot", author=author)
chat.process_message("slack", "slack:C123:T456", message)
```

## Opinionated API (progressive disclosure)

`ChatApp` is the high-level API for the common path: webhook -> typed context -> LangGraph run.

```python
from lsmsg_rs import ChatApp

chat = ChatApp(
    api_base_url="https://your-langgraph-api",
    assistants={
        "default": "assistant-default-id",
        "planner": "assistant-planner-id",
    },
    slack_signing_secret="...",
    teams_app_id="...",
)

# One-line ASGI app with built-in mention + /agent routing
app = chat.asgi_app()
```

Default behavior:

- `@mention` and Teams mention events trigger runs
- Slack `/agent` returns a fast ack and triggers a run
- `assistant_hint` chooses assistant alias (`@bot planner ...` -> `"planner"`)
- Unknown hints fall back to `"default"`

Progressive disclosure hooks:

```python
@chat.select_assistant
def pick_assistant(ctx):
    if ctx.provider == "slack" and "review" in ctx.text:
        return "planner"
    return "default"

@chat.build_input
def build_input(ctx, assistant):
    return {"messages": [{"role": "user", "content": f"{assistant}: {ctx.text}"}]}
```

## Starlette webhook bridge (decorators + typed context)

`ChatBridge` gives a `chat-ts`-style integration surface for webhook ingestion.
It supports:

- standalone ASGI app via `asgi_app()`
- extending an existing Starlette app via `register_routes(...)`
- mounting a sub-app via `mount(...)`
- frozen, typed route contexts (`SlackRouteCtx | TeamsRouteCtx`)

```python
from lsmsg_rs import ChatBridge, SlackAck, SlackRouteCtx, TeamsRouteCtx

bridge = ChatBridge(slack_signing_secret="...", teams_app_id="...")

@bridge.on_mention(provider="slack")
async def on_slack(ctx: SlackRouteCtx) -> None:
    # dynamic assistant hint from "@bot <assistant> ..."
    assistant = ctx.assistant_hint or "default"
    print("slack", assistant, ctx.thread_key, ctx.text)

@bridge.on_mention(provider="teams")
async def on_teams(ctx: TeamsRouteCtx) -> None:
    assistant = ctx.assistant_hint or "default"
    print("teams", assistant, ctx.thread_key, ctx.text)

@bridge.on_command("/agent")
async def slash_agent(ctx: SlackRouteCtx) -> SlackAck:
    return SlackAck(text=f"Routing to {ctx.assistant_hint or 'default'}")

# Option A: standalone ASGI app
app = bridge.asgi_app()
```

Security defaults are fail-closed:

- Slack webhooks require `slack_signing_secret`
- Teams webhooks require `teams_app_id` + Bot Framework bearer token validation
- Use `allow_unsigned_slack=True` / `allow_unauthenticated_teams=True` only for local testing

Use an existing Starlette app instead:

```python
from starlette.applications import Starlette

app = Starlette()
bridge = ChatBridge(slack_signing_secret="...", teams_app_id="...")

# Option B: add routes directly (inherits app middleware)
bridge.register_routes(app, prefix="/chat")

# Option C: mount sub-app
# bridge.mount(app, path="/chat")
```

## Real adapters

`lsmsg-rs` includes HTTP-backed adapters for Slack and Discord:

```python
from lsmsg_rs import Chat, SlackAdapter, DiscordAdapter

chat = Chat(user_name="bot")

chat.add_adapter(
    SlackAdapter(
        bot_token="xoxb-...",
        user_name="bot",
        bot_user_id="U123",
    )
)

chat.add_adapter(
    DiscordAdapter(
        bot_token="discord-bot-token",
        user_name="bot",
        bot_user_id="1234567890",
    )
)
```

For LangGraph run dispatch (thread mapping + run triggering), use `LangGraphAdapter`:

```python
from lsmsg_rs import LangGraphAdapter

langgraph = LangGraphAdapter(
    api_base_url="https://your-langgraph-api",
    assistant_id="your-assistant-id-or-graph-name",
    api_key="...",
)

# Stable provider-thread -> LangGraph thread mapping (UUIDv5)
thread_id = langgraph.thread_id(
    provider="slack",
    workspace_id="T123",
    channel_id="C456",
    root_thread_id="1710000000.100",
)

# Create/update thread and trigger run
result = langgraph.trigger_run(
    provider="slack",
    workspace_id="T123",
    channel_id="C456",
    root_thread_id="1710000000.100",
    input={"messages": [{"role": "user", "content": "hello"}]},
)
```

## Wheel builds

CI workflow: `.github/workflows/wheels.yml`

It builds:

- Linux wheels (manylinux)
- macOS wheels
- Windows wheels
- source distribution (sdist)
