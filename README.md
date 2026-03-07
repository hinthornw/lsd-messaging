# lsmsg

A unified messaging SDK that wires chat platforms to [LangGraph](https://github.com/langchain-ai/langgraph) agents. One Rust core, three language SDKs (Python, TypeScript, Go).

## Architecture

```
Slack ─┐                            ┌─ event.invoke("agent")
Teams ─┤──▶ SDK (Python/TS/Go) ──▶ Event ┤─ event.stream("agent")
       └────────────────────────────└─ event.reply("done")
```

All webhook parsing, signature verification, handler matching, and LangGraph API communication lives in a shared Rust core (`crates/lsmsg-core/`). Each language SDK wraps it with idiomatic APIs:

| SDK | Binding | Async model |
|-----|---------|-------------|
| Python | PyO3 (cdylib) | `asyncio` + `to_thread()` for FFI |
| TypeScript | napi-rs | Promises |
| Go | C FFI via cgo | goroutines |

## Quickstart (Python)

```bash
pip install lsmsg
```

```python
from lsmsg import Bot

bot = Bot(slack_signing_secret="...", slack_bot_token="xoxb-...")

@bot.mention
async def handle(event) -> None:
    result = await event.invoke("my-agent")
    await event.reply(result.text)

@bot.command("/summarize")
async def summarize(event) -> None:
    chunks = await event.stream("summarizer")
    await event.reply(chunks[-1].text)
```

The bot is an ASGI app — run it with uvicorn, or mount it into an existing Starlette/FastAPI app via `bot.attach(app)`.

## Quickstart (TypeScript)

```typescript
import { Bot } from 'lsmsg';

const bot = new Bot({
  slack: { signingSecret: '...', botToken: 'xoxb-...' },
  langGraph: { url: 'http://localhost:8123' },
});

bot.onMention(async (event) => {
  const result = await event.invoke('my-agent');
  await event.reply(result.text);
});

// Use as Express middleware
app.use('/webhooks', bot.expressMiddleware());
```

## Quickstart (Go)

```go
bot := lsmsg.NewBot(lsmsg.BotConfig{
    Slack: &lsmsg.SlackConfig{SigningSecret: "...", BotToken: "xoxb-..."},
    LangGraph: &lsmsg.LangGraphConfig{URL: "http://localhost:8123"},
})

bot.OnMention(func(event *lsmsg.Event) error {
    result, _ := event.Invoke("my-agent")
    return event.Reply(result.Text())
})

http.ListenAndServe(":8080", bot)
```

## Events and handlers

Each SDK supports the same handler types:

| Decorator/Method | Matches |
|-----------------|---------|
| `mention` / `onMention` / `OnMention` | Bot @-mentions |
| `message` / `onMessage` / `OnMessage` | Any channel message |
| `command` / `command` / `Command` | Slash commands |
| `reaction` / `onReaction` / `OnReaction` | Emoji reactions |
| `on` / `on` / `On` | Raw platform events |

All handlers accept optional `pattern` (regex) and `platform` filters.

## Platforms

| Platform | Webhook parsing | Signature verification |
|----------|----------------|----------------------|
| Slack | ✓ | ✓ (HMAC-SHA256) |
| Teams | ✓ | — |

## Project structure

```
crates/
  lsmsg-core/     Rust core — parsing, matching, LangGraph client
  lsmsg-py/       PyO3 bindings
  lsmsg-napi/     napi-rs bindings (Node.js)
  lsmsg-ffi/      C FFI bindings (Go)
sdks/
  python/         Python SDK (ASGI, asyncio)
  typescript/     TypeScript SDK (Express middleware)
  go/             Go SDK (net/http)
app/              Example LangGraph bot
```

## Development

```bash
make test            # run all tests (Rust + Python + TypeScript + Go)
make test-rust       # cargo test
make test-python     # pytest
make test-typescript # vitest
make test-go         # go test (builds Rust FFI first)
make lint            # cargo fmt/clippy + ruff + tsc
make format          # auto-format Rust + Python
make build           # build all artifacts
make clean           # remove build artifacts
```
