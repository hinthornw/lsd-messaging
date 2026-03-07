from __future__ import annotations

import hashlib
import hmac
import json
import time

from lsmsg._bot import (
    Bot,
    _parse_slack_webhook_python,
    _verify_slack_signature_python,
)
from lsmsg._types import Event, PlatformCapabilities, UserInfo


MENTION_BODY = json.dumps(
    {
        "type": "event_callback",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "text": "<@UBOT> hello benchmark",
            "channel": "C1",
            "ts": "123.456",
            "user": "U1",
        },
    },
    separators=(",", ":"),
).encode()
FORM_BODY = (
    b"command=%2Fecho&text=hello+world&team_id=T1&channel_id=C1"
    b"&user_id=U1&trigger_id=trig1"
)
TIMESTAMP = str(int(time.time()))
SIGNATURE = "v0=" + hmac.new(
    b"test-secret",
    b"v0:" + TIMESTAMP.encode("utf-8") + b":" + MENTION_BODY,
    hashlib.sha256,
).hexdigest()


def _make_event() -> Event:
    return Event(
        kind="mention",
        platform=PlatformCapabilities(
            name="slack",
            ephemeral=True,
            threads=True,
            reactions=True,
            streaming=True,
            modals=True,
            typing_indicator=True,
        ),
        workspace_id="T1",
        channel_id="C1",
        thread_id="t1",
        message_id="m1",
        user=UserInfo(id="U1", name="Alice"),
        text="please benchmark hello world",
        internal_thread_id="bench-thread",
    )


def _make_bot(handler_count: int = 64) -> Bot:
    bot = Bot()

    async def handler(event: Event) -> None:
        return None

    for i in range(handler_count):
        bot.message(handler, pattern=fr"token-{i}")
    bot.mention(handler, pattern=r"benchmark")
    return bot


def test_parse_slack_event_callback(benchmark) -> None:
    result = benchmark(_parse_slack_webhook_python, MENTION_BODY, "application/json")
    assert result["type"] == "event"
    assert result["event"]["kind"] == "mention"


def test_parse_slack_slash_command(benchmark) -> None:
    result = benchmark(
        _parse_slack_webhook_python,
        FORM_BODY,
        "application/x-www-form-urlencoded",
    )
    assert result["type"] == "event"
    assert result["event"]["command"] == "/echo"


def test_verify_slack_signature(benchmark) -> None:
    assert (
        benchmark(
            _verify_slack_signature_python,
            "test-secret",
            TIMESTAMP,
            SIGNATURE,
            MENTION_BODY,
        )
        is True
    )


def test_match_event_python(benchmark) -> None:
    bot = _make_bot()
    event = _make_event()
    matched = benchmark(bot._match_event_python, event)
    assert len(matched) == 1
