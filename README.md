# lsmsg

A unified messaging SDK that wires chat platforms to [LangGraph](https://github.com/langchain-ai/langgraph) agents. Typed events, async handlers, and minimal glue code.

## Why

Every chat platform has its own webhook format, auth scheme, and quirks. If you're building an AI agent that lives in Slack and Teams, you end up writing the same webhook→agent→reply plumbing repeatedly — then maintaining it forever.

**lsmsg** absorbs that complexity. You write handlers against typed Python events. The SDK handles webhook parsing, signature verification, platform capability differences, and agent orchestration. Your bot code stays the same whether it's running on one platform or seven.

```
Slack ─┐                       ┌─ event.invoke("agent")
Teams ─┤──▶ Bot (ASGI) ──▶ Event ┤─ event.stream("agent")
       └───────────────────────└─ event.reply("done")
```

## Quickstart

```bash
pip install lsmsg
```

```python
from lsmsg import Bot, MentionEvent, CommandEvent, RunChunk, Slack

bot = Bot(slack=Slack())  # reads env vars automatically

@bot.mention
async def handle(event: MentionEvent) -> None:
    # Sends {"messages": [{"role": "user", "content": event.text}]}
    # to the "my-agent" assistant, using a deterministic thread ID
    # derived from the platform conversation.
    result = await event.invoke("my-agent")
    await event.reply(result.text)

@bot.command("/summarize")
async def summarize(event: CommandEvent) -> None:
    async for chunk in event.stream("summarizer"):
        await event.reply(chunk.text_delta)
```

Start the LangGraph dev server (`make dev`) and the bot's message handlers are included as additional routes in the API server. Calls to `invoke` or `stream` forward messagesto the queue to be executed by the assistant's worker pool.

Slack webhooks POST to `/slack/events`, the bot parses them into typed events, calls your handler, and you talk to your agent.

## Platforms

Webhook ingestion in `Bot` currently supports Slack and Teams. Pass platform configs to `Bot()`:

```python
from lsmsg import Bot, Slack, Teams

bot = Bot(
    slack=Slack(),          # SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN
    teams=Teams(),          # TEAMS_APP_ID, TEAMS_APP_PASSWORD, TEAMS_TENANT_ID
)
```

Or pass values explicitly:

```python
bot = Bot(
    slack=Slack(signing_secret="xoxs-...", bot_token="xoxb-..."),
)
```

| Platform | Config class | Env vars |
|----------|-------------|----------|
| Slack | `Slack()` | `SLACK_SIGNING_SECRET`, `SLACK_BOT_TOKEN` |
| Teams | `Teams()` | `TEAMS_APP_ID`, `TEAMS_APP_PASSWORD`, `TEAMS_TENANT_ID` |

Other platform config classes are reserved for future webhook adapters and currently raise `NotImplementedError` when passed to `Bot`.

## Events and handlers

Handlers are registered with decorators. Each decorator matches a specific event type:

```python
@bot.mention
async def on_mention(event: MentionEvent) -> None: ...    # someone @-mentions the bot

@bot.message
async def on_message(event: MessageEvent) -> None: ...    # any message in a channel

@bot.command("/deploy")
async def on_deploy(event: CommandEvent) -> None: ...     # slash command

@bot.reaction("eyes")
async def on_eyes(event: ReactionEvent) -> None: ...      # emoji reaction

@bot.on("file_shared")
async def on_file(event: RawEvent) -> None: ...           # raw platform event
```

You can filter by pattern or platform:

```python
@bot.mention(pattern=r"deploy (\w+)")
async def deploy_env(event: MentionEvent) -> None: ...

@bot.message(platform="slack")
async def slack_only(event: MessageEvent) -> None: ...
```

Every event carries the same shape:

| Field | Type | Description |
|-------|------|-------------|
| `event.text` | `str` | Message text (cleaned of mention tags) |
| `event.user` | `UserInfo` | `.id`, `.name`, `.email` |
| `event.channel_id` | `str` | Channel/conversation ID |
| `event.thread_id` | `str` | Thread ID (platform-native) |
| `event.platform` | `PlatformCapabilities` | `.name`, `.ephemeral`, `.threads`, etc. |
| `event.raw` | `Mapping[str, Any]` | Original webhook payload |

## Talking to agents

Every event has three ways to run a LangGraph assistant. By default, the input is `{"messages": [{"role": "user", "content": event.text}]}` — the message that triggered the event, formatted for the LangGraph messages state. Thread IDs are deterministic (derived from platform + workspace + channel + thread), so agents maintain conversation context across messages.

```python
@bot.mention
async def handle(event: MentionEvent) -> None:
    # Simple request/response — sends event.text as a user message
    result: RunResult = await event.invoke("my-agent")
    print(result.text)       # last assistant message content
    print(result.output)     # full LangGraph state

@bot.mention
async def handle_stream(event: MentionEvent) -> None:
    # Streaming — token-level deltas via stream_mode="messages"
    chunk: RunChunk
    async for chunk in event.stream("my-agent"):
        print(chunk.text_delta, end="")  # incremental text
        print(chunk.text)                # accumulated text so far

@bot.mention
async def handle_lifecycle(event: MentionEvent) -> None:
    # Full lifecycle control
    run: Run = await event.start("my-agent")
    # ... do other work ...
    result: RunResult = await run.wait()
    # or: async for chunk in run.stream(): ...
    # or: await run.cancel()
```

Pass custom input to override the default:

```python
result = await event.invoke(
    "my-agent",
    input={"messages": [{"role": "user", "content": "custom prompt"}]},
    config={"configurable": {"model": "gpt-4o"}},
    metadata={"user_id": event.user.id},
)
```

## Replying

```python
# Visible reply in the thread
sent: SentMessage = await event.reply("Here's what I found:")

# Update the reply later
await sent.update("Here's the updated answer:")

# Delete it
await sent.delete()

# Ephemeral message (only the user sees it)
await event.whisper("Processing your request...")

# Graceful fallback when platform doesn't support ephemeral
await event.whisper("Working...", fallback="reply")
```

## Slash commands and auto-ack

Slash commands in Slack require a response within 3 seconds. lsmsg handles this automatically:

```python
@bot.command("/analyze")                        # auto-acks with "Working..."
async def analyze(event: CommandEvent) -> None:
    result = await event.invoke("analyzer")     # can take as long as needed
    await event.reply(result.text)

@bot.command("/status", ack="Checking...")       # custom ack text
async def status(event: CommandEvent) -> None: ...

@bot.command("/quick", ack=False)                # manual ack (you return the ack payload)
async def quick(event: CommandEvent) -> None: ...

@bot.command("/quick", ack=False)
async def quick(event: CommandEvent) -> None:
    await event.ack("On it")
```

## Platform capabilities

Not every platform supports every feature. lsmsg models this explicitly:

```python
@bot.mention
async def handle(event: MentionEvent) -> None:
    if event.platform.threads:
        await event.reply("Replying in thread")

    if event.platform.ephemeral:
        await event.whisper("Only you see this")
    else:
        # whisper() raises PlatformNotSupported on Teams, Discord, etc.
        # unless you opt into fallback:
        await event.whisper("Falling back to reply", fallback="reply")
```

| Capability | Slack | Teams | Discord | Telegram | GitHub | Linear | GChat |
|-----------|-------|-------|---------|----------|--------|--------|-------|
| Threads | ✓ | ✓ | ✓ | | ✓ | ✓ | ✓ |
| Ephemeral | ✓ | | | | | | ✓ |
| Reactions | ✓ | | ✓ | ✓ | | ✓ | ✓ |
| Streaming | ✓ | | | | | | |
| Modals | ✓ | | | | | | |
| Typing | ✓ | ✓ | ✓ | ✓ | | | ✓ |

## Mounting into an existing app

If you already have a Starlette/FastAPI app:

```python
from starlette.applications import Starlette

app = Starlette()
bot.attach(app, prefix="/chat")
# Webhooks now live at /chat/slack/events, /chat/teams/events, etc.
```

Or use the bot directly as an ASGI app — it creates its own Starlette under the hood.

## Testing

lsmsg ships a test client that simulates webhook events without any network calls:

```python
from lsmsg.testing import BotTestClient

client = BotTestClient(bot)

result = client.mention("hello bot")
assert result.status_code == 200

result = client.command("/deploy", "production")
assert result.ack_text == "Working..."

result = client.message("hello", platform="teams")
assert result.status_code == 200
```

## Development

```bash
make install     # uv sync
make test        # pytest
make test-watch  # pytest-watch (re-runs on save)
make dev         # langgraph dev server on :2024
make format      # ruff format + ruff check --fix
make lint        # ruff check + ty + cargo clippy
make build       # build wheels
make clean       # remove all build artifacts
```

## Project structure

```
src/lsmsg/        Python package source
tests/            Unit and integration tests
lsmsg-rs/         Rust extension crate (optional)
```
