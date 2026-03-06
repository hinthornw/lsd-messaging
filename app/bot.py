"""Bot with platform integrations — runs inside the LangGraph dev server."""

from starlette.applications import Starlette

from lsmsg import Bot, CommandEvent, MentionEvent, MessageEvent, Slack

bot = Bot(
    slack=Slack(signing_secret="dev-secret", bot_token="xoxb-dev"),
    # teams=Teams(),
    # discord=Discord(),
)


@bot.mention
async def on_mention(event: MentionEvent) -> None:
    result = await event.invoke("echo")
    await event.reply(result.text)


@bot.command("/echo")
async def on_echo(event: CommandEvent) -> None:
    result = await event.invoke("echo")
    await event.reply(result.text)


@bot.message
async def on_message(event: MessageEvent) -> None:
    async for chunk in event.stream("echo"):
        if chunk.text_delta:
            await event.reply(chunk.text_delta)


app = Starlette()
bot.attach(app)
