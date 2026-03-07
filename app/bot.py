"""Bot with platform integrations — runs inside the LangGraph dev server."""

from starlette.applications import Starlette

from lsmsg import Bot

bot = Bot(
    slack_signing_secret="dev-secret",
    slack_bot_token="xoxb-dev",
    langgraph_url="http://localhost:2024",
)


@bot.mention
async def on_mention(event) -> None:
    result = await event.invoke("echo")
    await event.reply(result.text)


@bot.command("/echo")
async def on_echo(event) -> None:
    result = await event.invoke("echo")
    await event.reply(result.text)


@bot.message
async def on_message(event) -> None:
    chunks = await event.stream("echo")
    if chunks:
        await event.reply(chunks[-1].text)


app = Starlette()
bot.attach(app)
