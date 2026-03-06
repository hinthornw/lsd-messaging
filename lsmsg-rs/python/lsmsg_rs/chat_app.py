from __future__ import annotations

import asyncio
import inspect
import os
import re
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol, TypeAlias

from ._lsmsg_rs import LangGraphAdapter
from .bridge import ChatBridge, Provider, RouteCtx, SlackAck


class _RunAdapter(Protocol):
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
        ...


AssistantSelector: TypeAlias = Callable[[RouteCtx], Awaitable[str | None] | str | None]
InputBuilder: TypeAlias = Callable[
    [RouteCtx, str],
    Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None,
]
MetadataBuilder: TypeAlias = Callable[
    [RouteCtx, str],
    Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None,
]
ConfigBuilder: TypeAlias = Callable[
    [RouteCtx, str],
    Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None,
]
AdapterFactory: TypeAlias = Callable[[str], _RunAdapter]


class ChatApp:
    """Opinionated unified chat API with progressive disclosure.

    The class composes:
    - `ChatBridge` (Slack + Teams webhook normalization and routing)
    - one `LangGraphAdapter` per assistant alias

    Common case:
    - instantiate once
    - expose webhook routes via Starlette
    - default mention/command routes trigger LangGraph runs

    Advanced use:
    - custom assistant selector
    - custom input/config/metadata builders
    - custom bridge routes and direct adapter access
    """

    def __init__(
        self,
        *,
        api_base_url: str,
        assistants: Mapping[str, str] | None = None,
        assistant_id: str | None = None,
        default_assistant: str | None = None,
        api_key: str | None = None,
        thread_namespace: str | None = None,
        slack_signing_secret: str | None = None,
        background_dispatch: bool = True,
        auto_routes: bool = True,
        default_command: str | None = "/agent",
        include_message_events: bool = False,
        multitask_strategy: str = "enqueue",
        if_not_exists: str = "create",
        webhook: str | None = None,
        durability: str | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        assistant_map = _normalize_assistants(assistants=assistants, assistant_id=assistant_id)
        if default_assistant is None:
            default_assistant = next(iter(assistant_map))
        if default_assistant not in assistant_map:
            raise ValueError(
                f"default_assistant '{default_assistant}' is not in assistants: {sorted(assistant_map)}"
            )

        self.bridge = ChatBridge(
            slack_signing_secret=slack_signing_secret,
            background_dispatch=background_dispatch,
        )
        self._assistant_ids = assistant_map
        self._assistant_alias_by_lower = {alias.lower(): alias for alias in assistant_map}
        self._default_assistant = default_assistant
        self._multitask_strategy = multitask_strategy
        self._if_not_exists = if_not_exists
        self._webhook = _clean_optional(webhook)
        self._durability = _clean_optional(durability)
        self._pending_tasks: set[asyncio.Task[Any]] = set()

        if adapter_factory is None:
            adapter_factory = _langgraph_adapter_factory(
                api_base_url=api_base_url,
                api_key=api_key,
                thread_namespace=thread_namespace,
            )

        self._adapters: dict[str, _RunAdapter] = {
            alias: adapter_factory(assistant_id_value)
            for alias, assistant_id_value in assistant_map.items()
        }

        self._assistant_selector: AssistantSelector = self._default_selector
        self._input_builder: InputBuilder = _default_input_builder
        self._thread_metadata_builder: MetadataBuilder = _default_thread_metadata_builder
        self._run_metadata_builder: MetadataBuilder = _default_run_metadata_builder
        self._config_builder: ConfigBuilder = _default_config_builder

        if auto_routes:
            self.enable_default_routes(
                default_command=default_command,
                include_message_events=include_message_events,
            )

    @classmethod
    def from_env(
        cls,
        *,
        assistants: Mapping[str, str] | None = None,
        assistant_id: str | None = None,
        default_assistant: str | None = None,
        slack_signing_secret: str | None = None,
        background_dispatch: bool = True,
        auto_routes: bool = True,
        default_command: str | None = "/agent",
        include_message_events: bool = False,
        multitask_strategy: str = "enqueue",
        if_not_exists: str = "create",
        webhook: str | None = None,
        durability: str | None = None,
    ) -> "ChatApp":
        api_base_url = _required_env("LANGGRAPH_API_URL")
        api_key = os.getenv("LANGGRAPH_API_KEY")
        thread_namespace = os.getenv("LANGGRAPH_THREAD_NAMESPACE")

        if assistants is None and assistant_id is None:
            env_assistant_id = os.getenv("LANGGRAPH_ASSISTANT_ID")
            if env_assistant_id:
                assistant_id = env_assistant_id

        if slack_signing_secret is None:
            slack_signing_secret = os.getenv("SLACK_SIGNING_SECRET")

        return cls(
            api_base_url=api_base_url,
            assistants=assistants,
            assistant_id=assistant_id,
            default_assistant=default_assistant,
            api_key=api_key,
            thread_namespace=thread_namespace,
            slack_signing_secret=slack_signing_secret,
            background_dispatch=background_dispatch,
            auto_routes=auto_routes,
            default_command=default_command,
            include_message_events=include_message_events,
            multitask_strategy=multitask_strategy,
            if_not_exists=if_not_exists,
            webhook=webhook,
            durability=durability,
        )

    @property
    def assistants(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self._assistant_ids))

    @property
    def default_assistant(self) -> str:
        return self._default_assistant

    @property
    def adapters(self) -> Mapping[str, _RunAdapter]:
        return MappingProxyType(dict(self._adapters))

    def asgi_app(self):
        return self.bridge.asgi_app()

    def register_routes(self, app: Any, *, prefix: str = "/chat") -> None:
        self.bridge.register_routes(app, prefix=prefix)

    def mount(self, app: Any, *, path: str = "/chat") -> None:
        self.bridge.mount(app, path=path)

    def on_mention(self, *, provider: Provider | None = None):
        return self.bridge.on_mention(provider=provider)

    def on_message(self, *, provider: Provider | None = None):
        return self.bridge.on_message(provider=provider)

    def on_command(self, command: str):
        return self.bridge.on_command(command)

    def on_event(self, *, event_type: str = "*", provider: Provider | None = None):
        return self.bridge.on_event(event_type=event_type, provider=provider)

    def select_assistant(self, selector: AssistantSelector) -> AssistantSelector:
        if not callable(selector):
            raise TypeError("selector must be callable")
        self._assistant_selector = selector
        return selector

    def build_input(self, builder: InputBuilder) -> InputBuilder:
        if not callable(builder):
            raise TypeError("builder must be callable")
        self._input_builder = builder
        return builder

    def build_thread_metadata(self, builder: MetadataBuilder) -> MetadataBuilder:
        if not callable(builder):
            raise TypeError("builder must be callable")
        self._thread_metadata_builder = builder
        return builder

    def build_run_metadata(self, builder: MetadataBuilder) -> MetadataBuilder:
        if not callable(builder):
            raise TypeError("builder must be callable")
        self._run_metadata_builder = builder
        return builder

    def build_config(self, builder: ConfigBuilder) -> ConfigBuilder:
        if not callable(builder):
            raise TypeError("builder must be callable")
        self._config_builder = builder
        return builder

    def enable_default_routes(
        self,
        *,
        default_command: str | None = "/agent",
        include_message_events: bool = False,
    ) -> None:
        @self.on_mention()
        async def _on_mention(ctx: RouteCtx) -> None:
            await self.trigger(ctx)

        if include_message_events:
            @self.on_message()
            async def _on_message(ctx: RouteCtx) -> None:
                await self.trigger(ctx)

        if default_command:
            @self.on_command(default_command)
            async def _on_command(ctx: RouteCtx) -> SlackAck:
                assistant = await self.resolve_assistant(ctx)
                self._spawn_background(self.trigger(ctx, assistant=assistant))
                return SlackAck(
                    text=f"Running {assistant}...",
                    response_type="ephemeral",
                )

    async def resolve_assistant(self, ctx: RouteCtx, *, assistant: str | None = None) -> str:
        if assistant is not None:
            resolved = self._resolve_alias(assistant)
            if resolved is None:
                raise ValueError(
                    f"unknown assistant '{assistant}', expected one of: {sorted(self._assistant_ids)}"
                )
            return resolved

        selected = await _maybe_await(self._assistant_selector(ctx))
        if selected is not None:
            resolved = self._resolve_alias(selected)
            if resolved is not None:
                return resolved

        if ctx.assistant_hint:
            resolved = self._resolve_alias(ctx.assistant_hint)
            if resolved is not None:
                return resolved

        return self._default_assistant

    async def trigger(
        self,
        ctx: RouteCtx,
        *,
        assistant: str | None = None,
        input: Mapping[str, Any] | None = None,
        thread_metadata: Mapping[str, Any] | None = None,
        run_metadata: Mapping[str, Any] | None = None,
        config: Mapping[str, Any] | None = None,
        multitask_strategy: str | None = None,
        if_not_exists: str | None = None,
        webhook: str | None = None,
        durability: str | None = None,
    ) -> Mapping[str, Any]:
        alias = await self.resolve_assistant(ctx, assistant=assistant)
        adapter = self._adapters[alias]

        resolved_input = input if input is not None else await _maybe_await(
            self._input_builder(ctx, alias)
        )
        resolved_thread_metadata = (
            thread_metadata
            if thread_metadata is not None
            else await _maybe_await(self._thread_metadata_builder(ctx, alias))
        )
        resolved_run_metadata = run_metadata if run_metadata is not None else await _maybe_await(
            self._run_metadata_builder(ctx, alias)
        )
        resolved_config = config if config is not None else await _maybe_await(
            self._config_builder(ctx, alias)
        )

        result = adapter.trigger_run(
            provider=ctx.provider,
            workspace_id=ctx.workspace_id,
            channel_id=ctx.channel_id,
            root_thread_id=ctx.root_thread_id,
            input=_as_dict_or_none(resolved_input),
            thread_metadata=_as_dict_or_none(resolved_thread_metadata),
            run_metadata=_as_dict_or_none(resolved_run_metadata),
            config=_as_dict_or_none(resolved_config),
            multitask_strategy=multitask_strategy or self._multitask_strategy,
            if_not_exists=if_not_exists or self._if_not_exists,
            webhook=self._webhook if webhook is None else _clean_optional(webhook),
            durability=self._durability if durability is None else _clean_optional(durability),
        )
        return result

    def _resolve_alias(self, candidate: str) -> str | None:
        cleaned = candidate.strip()
        if not cleaned:
            return None
        direct = self._assistant_alias_by_lower.get(cleaned.lower())
        if direct is not None:
            return direct
        for alias, assistant_id in self._assistant_ids.items():
            if assistant_id == cleaned:
                return alias
        return None

    def _default_selector(self, ctx: RouteCtx) -> str | None:
        return ctx.assistant_hint

    def _spawn_background(self, awaitable: Awaitable[Any]) -> None:
        task = asyncio.create_task(awaitable)
        self._pending_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task[Any]) -> None:
        self._pending_tasks.discard(task)
        try:
            task.result()
        except Exception:
            pass


def _langgraph_adapter_factory(
    *,
    api_base_url: str,
    api_key: str | None,
    thread_namespace: str | None,
) -> AdapterFactory:
    def make(assistant_id: str) -> _RunAdapter:
        return LangGraphAdapter(
            api_base_url=api_base_url,
            assistant_id=assistant_id,
            api_key=api_key,
            thread_namespace=thread_namespace,
        )

    return make


def _normalize_assistants(
    *,
    assistants: Mapping[str, str] | None,
    assistant_id: str | None,
) -> dict[str, str]:
    if assistants is not None and assistant_id is not None:
        raise ValueError("pass either assistants or assistant_id, not both")

    if assistant_id is not None:
        cleaned_assistant_id = assistant_id.strip()
        if not cleaned_assistant_id:
            raise ValueError("assistant_id must be a non-empty string")
        return {"default": cleaned_assistant_id}

    if assistants is None:
        raise ValueError("assistants or assistant_id is required")

    normalized: dict[str, str] = {}
    for alias, assistant in assistants.items():
        clean_alias = alias.strip()
        clean_assistant = assistant.strip()
        if not clean_alias:
            raise ValueError("assistant alias must be non-empty")
        if not clean_assistant:
            raise ValueError(f"assistant id for alias '{alias}' must be non-empty")
        if clean_alias in normalized:
            raise ValueError(f"duplicate assistant alias '{clean_alias}'")
        normalized[clean_alias] = clean_assistant

    if not normalized:
        raise ValueError("assistants must not be empty")
    return normalized


def _default_input_builder(ctx: RouteCtx, assistant: str) -> Mapping[str, Any]:
    prompt = _clean_prompt_text(ctx.text)
    if ctx.assistant_hint and ctx.assistant_hint.lower() == assistant.lower():
        prompt = _drop_first_token(prompt)
    return {"messages": [{"role": "user", "content": prompt}]}


def _default_thread_metadata_builder(ctx: RouteCtx, _assistant: str) -> Mapping[str, Any]:
    return {
        "chat_provider": ctx.provider,
        "chat_workspace_id": ctx.workspace_id,
        "chat_channel_id": ctx.channel_id,
        "chat_root_thread_id": ctx.root_thread_id,
    }


def _default_run_metadata_builder(ctx: RouteCtx, assistant: str) -> Mapping[str, Any]:
    return {
        "chat_provider": ctx.provider,
        "chat_event_type": ctx.event_type,
        "chat_message_id": ctx.message_id,
        "chat_user_id": ctx.user_id,
        "chat_assistant": assistant,
    }


def _default_config_builder(_ctx: RouteCtx, _assistant: str) -> Mapping[str, Any] | None:
    return None


def _clean_prompt_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^\s*<@[^>]+>\s*", "", cleaned)
    cleaned = re.sub(r"^\s*<at>.*?</at>\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _drop_first_token(text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) <= 1:
        return ""
    return parts[1].strip()


async def _maybe_await(value: Awaitable[Any] | Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        raise ValueError(f"missing required environment variable: {name}")
    return value.strip()


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _as_dict_or_none(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)


__all__ = [
    "ChatApp",
]
