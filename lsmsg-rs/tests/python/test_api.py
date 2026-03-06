import uuid

import pytest

from lsmsg_rs import (
    Author,
    Chat,
    DiscordAdapter,
    InMemoryAdapter,
    LangGraphAdapter,
    Message,
    SlackAdapter,
)


@pytest.fixture
def chat_and_adapter():
    chat = Chat(user_name="bot", dedupe_ttl_ms=60_000, lock_ttl_ms=60_000)
    adapter = InMemoryAdapter(name="slack", user_name="bot", bot_user_id="U_BOT")
    chat.add_adapter(adapter)
    return chat, adapter


def build_message(message_id: str, text: str, thread_id: str = "slack:C1:T1") -> Message:
    author = Author(user_id="U1", user_name="alice", full_name="Alice")
    return Message(id=message_id, thread_id=thread_id, text=text, author=author)


def test_author_and_message_surface() -> None:
    author = Author(user_id="U1", user_name="alice", full_name="Alice", is_bot=False, is_me=False)
    assert author.user_id == "U1"
    assert author.user_name == "alice"
    assert author.full_name == "Alice"
    assert author.is_bot is False
    assert author.is_me is False
    assert author.to_dict()["user_id"] == "U1"

    message = Message(id="m1", thread_id="slack:C1:T1", text="hello", author=author, is_mention=True)
    assert message.id == "m1"
    assert message.thread_id == "slack:C1:T1"
    assert message.text == "hello"
    assert message.is_mention is True
    assert message.author.user_name == "alice"
    assert message.to_dict()["text"] == "hello"


def test_thread_channel_sent_message_state_and_adapter_apis(chat_and_adapter) -> None:
    chat, adapter = chat_and_adapter

    thread = chat.thread("slack", "slack:C1:T1")
    assert thread.id == "slack:C1:T1"
    assert thread.channel_id == "slack:C1"
    assert thread.adapter_name == "slack"
    assert thread.is_dm is False

    assert thread.is_subscribed() is False
    thread.subscribe()
    assert thread.is_subscribed() is True
    thread.unsubscribe()
    assert thread.is_subscribed() is False

    sent = thread.post("hello thread")
    assert sent.thread_id == "slack:C1:T1"
    assert sent.text == "hello thread"

    sent.edit({"markdown": "edited markdown"})
    assert sent.text == "edited markdown"

    sent.add_reaction(":thumbsup:")
    sent.remove_reaction(":thumbsup:")

    fetched_thread = thread.recent_messages(limit=20)
    assert len(fetched_thread) == 1
    assert fetched_thread[0].text == "edited markdown"

    refreshed = thread.refresh(limit=20)
    assert len(refreshed) == 1

    restored = sent.to_message()
    assert restored.id == sent.id
    assert restored.text == "edited markdown"

    thread.set_state({"a": 1})
    thread.set_state({"b": 2})
    assert thread.state == {"a": 1, "b": 2}

    thread.set_state({"only": True}, replace=True)
    assert thread.state == {"only": True}

    assert thread.mention_user("U2") == "<@U2>"

    channel = thread.channel()
    assert channel.id == "slack:C1"
    assert channel.adapter_name == "slack"
    assert channel.is_dm is False

    channel_sent = channel.post({"text": "hello channel"})
    assert channel_sent.text == "hello channel"
    assert channel_sent.thread_id == "slack:C1"

    channel_messages = channel.messages(limit=20)
    assert len(channel_messages) == 1
    assert channel_messages[0].text == "hello channel"

    channel.set_state({"x": 1})
    channel.set_state({"y": 2})
    assert channel.state == {"x": 1, "y": 2}

    channel.set_state({"z": 3}, replace=True)
    assert channel.state == {"z": 3}

    assert channel.mention_user("U2") == "<@U2>"

    adapter_messages = adapter.fetch_messages("slack:C1:T1", limit=20)
    assert len(adapter_messages) == 1
    assert adapter_messages[0].id == sent.id

    sent.delete()
    assert thread.recent_messages(limit=20) == []


def test_chat_event_routing_and_dedupe(chat_and_adapter) -> None:
    chat, _ = chat_and_adapter
    calls = []

    chat.on_new_mention(lambda thread, message: calls.append(("mention", thread.id, message.id)))
    chat.on_new_message(r"^!help$", lambda thread, message: calls.append(("pattern", thread.id, message.id)))
    chat.on_subscribed_message(lambda thread, message: calls.append(("subscribed", thread.id, message.id)))

    m1 = build_message("m1", "hi @bot")
    chat.process_message("slack", "slack:C1:T1", m1)
    assert calls == [("mention", "slack:C1:T1", "m1")]

    # Dedupe: same message id should not dispatch again.
    chat.process_message("slack", "slack:C1:T1", m1)
    assert len(calls) == 1

    m2 = build_message("m2", "!help")
    chat.process_message("slack", "slack:C1:T1", m2)
    assert calls[-1] == ("pattern", "slack:C1:T1", "m2")

    thread = chat.thread("slack", "slack:C1:T1")
    thread.subscribe()

    m3 = build_message("m3", "anything @bot")
    chat.process_message("slack", "slack:C1:T1", m3)
    assert calls[-1] == ("subscribed", "slack:C1:T1", "m3")


def test_chat_process_message_accepts_dict_and_string(chat_and_adapter) -> None:
    chat, _ = chat_and_adapter
    seen = []

    chat.on_new_message(r".*", lambda _thread, message: seen.append(message.text))

    chat.process_message(
        "slack",
        "slack:C1:T2",
        {
            "id": "dict-1",
            "text": "from dict",
            "author": {
                "user_id": "U1",
                "user_name": "alice",
                "full_name": "Alice",
            },
        },
    )

    chat.process_message("slack", "slack:C1:T2", "from string")

    assert "from dict" in seen
    assert "from string" in seen


def test_chat_validates_callbacks(chat_and_adapter) -> None:
    chat, _ = chat_and_adapter

    with pytest.raises(TypeError):
        chat.on_new_mention(123)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        chat.on_subscribed_message(None)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        chat.on_new_message(".*", 1.23)  # type: ignore[arg-type]


def test_chat_channel_invalid_id(chat_and_adapter) -> None:
    chat, _ = chat_and_adapter
    with pytest.raises(ValueError):
        chat.channel("")


def test_chat_accepts_real_adapters() -> None:
    chat = Chat(user_name="bot")

    slack = SlackAdapter(
        bot_token="xoxb-test",
        user_name="bot",
        bot_user_id="U_BOT",
        api_base_url="http://127.0.0.1:9999/api",
    )
    discord = DiscordAdapter(
        bot_token="discord-test",
        user_name="bot",
        bot_user_id="BOT",
        api_base_url="http://127.0.0.1:9998/api",
    )

    chat.add_adapter(slack)
    chat.add_adapter(discord)

    slack_thread = chat.thread("slack", "slack:C1:1710000000.1")
    discord_thread = chat.thread("discord", "discord:CHAN1:m1")

    assert slack_thread.adapter_name == "slack"
    assert slack_thread.channel_id == "slack:C1"
    assert discord_thread.adapter_name == "discord"
    assert discord_thread.channel_id == "discord:CHAN1"


def test_langgraph_adapter_thread_id_is_uuid5_deterministic() -> None:
    adapter = LangGraphAdapter(
        api_base_url="http://127.0.0.1:9997",
        assistant_id="assistant-a",
    )

    thread_id = adapter.thread_id(
        provider="slack",
        workspace_id="T123",
        channel_id="C456",
        root_thread_id="1710000000.100",
    )
    same_thread_id = adapter.thread_id(
        provider="slack",
        workspace_id="T123",
        channel_id="C456",
        root_thread_id="1710000000.100",
    )
    different_thread_id = adapter.thread_id(
        provider="slack",
        workspace_id="T123",
        channel_id="C456",
        root_thread_id="1710000000.200",
    )

    assert thread_id == same_thread_id
    assert thread_id != different_thread_id
    assert uuid.UUID(thread_id).version == 5


def test_langgraph_adapter_validates_inputs() -> None:
    adapter = LangGraphAdapter(
        api_base_url="http://127.0.0.1:9997",
        assistant_id="assistant-a",
    )

    with pytest.raises(ValueError):
        adapter.ensure_thread("not-a-uuid")

    with pytest.raises(ValueError):
        adapter.create_run("not-a-uuid")

    valid_thread_id = str(uuid.uuid4())
    with pytest.raises(ValueError):
        adapter.create_run(valid_thread_id, multitask_strategy="invalid")

    with pytest.raises(ValueError):
        adapter.trigger_run(
            provider="",
            workspace_id="T123",
            channel_id="C456",
            root_thread_id="1710000000.100",
        )
