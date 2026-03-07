package dev.lsmsg;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicLong;

public final class Bot implements AutoCloseable {
    private static final System.Logger LOGGER = System.getLogger(Bot.class.getName());

    private final BotConfig config;
    private final LangGraphClient langGraphClient;
    private final ExecutorService executor;
    private final Set<CompletableFuture<?>> pending;
    private final Map<Long, RegisteredHandler> handlers;
    private final AtomicLong nextHandlerId;

    public Bot(BotConfig config) {
        this.config = config;
        this.langGraphClient = config.langGraph() == null
                ? null
                : new LangGraphClient(config.langGraph().url(), config.langGraph().apiKey());
        this.executor = Executors.newVirtualThreadPerTaskExecutor();
        this.pending = ConcurrentHashMap.newKeySet();
        this.handlers = new ConcurrentHashMap<>();
        this.nextHandlerId = new AtomicLong(1);
    }

    public long onMention(EventHandler handler) {
        return onMention(null, handler);
    }

    public long onMention(HandlerOptions options, EventHandler handler) {
        return register(new RegisteredHandler(EventKind.MENTION, null, null, null, options, handler));
    }

    public long onMessage(EventHandler handler) {
        return onMessage(null, handler);
    }

    public long onMessage(HandlerOptions options, EventHandler handler) {
        return register(new RegisteredHandler(EventKind.MESSAGE, null, null, null, options, handler));
    }

    public long command(String name, EventHandler handler) {
        return command(name, null, handler);
    }

    public long command(String name, HandlerOptions options, EventHandler handler) {
        return register(new RegisteredHandler(EventKind.COMMAND, name, null, null, options, handler));
    }

    public long onReaction(String emoji, EventHandler handler) {
        return onReaction(emoji, null, handler);
    }

    public long onReaction(String emoji, HandlerOptions options, EventHandler handler) {
        return register(new RegisteredHandler(EventKind.REACTION, null, emoji, null, options, handler));
    }

    public long on(String rawEventType, EventHandler handler) {
        return on(rawEventType, null, handler);
    }

    public long on(String rawEventType, HandlerOptions options, EventHandler handler) {
        return register(new RegisteredHandler(EventKind.RAW, null, null, rawEventType, options, handler));
    }

    public boolean off(long handlerId) {
        return handlers.remove(handlerId) != null;
    }

    public void dispatch(Event event) {
        Event bound = event.bind(this);
        for (RegisteredHandler handler : handlers.values()) {
            if (!handler.matches(bound)) {
                continue;
            }
            try {
                handler.callback.handle(bound);
            } catch (Exception exc) {
                LOGGER.log(System.Logger.Level.ERROR, "Handler failed", exc);
            }
        }
    }

    public WebhookResponse handleSlackWebhook(byte[] body, String contentType, Map<String, String> headers) {
        BotConfig.SlackConfig slack = config.slack();
        if (slack != null && slack.signingSecret() != null && !slack.signingSecret().isBlank()) {
            String timestamp = header(headers, "x-slack-request-timestamp");
            String signature = header(headers, "x-slack-signature");
            if (timestamp.isBlank() || signature.isBlank()) {
                return WebhookResponse.json(401, Map.of("error", "missing signature headers"));
            }
            if (!Slack.verifySignature(slack.signingSecret(), timestamp, signature, body)) {
                return WebhookResponse.json(401, Map.of("error", "invalid signature"));
            }
        }

        SlackWebhookResult result = Slack.parseWebhook(body, contentType == null ? "application/json" : contentType);
        if (result instanceof SlackWebhookResult.Challenge challenge) {
            return WebhookResponse.json(200, Map.of("challenge", challenge.value()));
        }
        if (result instanceof SlackWebhookResult.EventPayload payload) {
            scheduleDispatch(payload.event());
        }
        return WebhookResponse.json(200, Map.of("ok", Boolean.TRUE));
    }

    public WebhookResponse handleSlackWebhook(String body, String contentType, Map<String, String> headers) {
        return handleSlackWebhook(body.getBytes(StandardCharsets.UTF_8), contentType, headers);
    }

    public WebhookResponse handleTeamsWebhook(String payloadJson) {
        Event event = Teams.parseWebhook(payloadJson);
        if (event != null) {
            scheduleDispatch(event);
        }
        return WebhookResponse.json(200, Map.of("ok", Boolean.TRUE));
    }

    public WebhookResponse handleTeamsWebhook(Map<String, Object> payload) {
        return handleTeamsWebhook(Json.stringify(payload));
    }

    public void drain(Duration timeout) {
        CompletableFuture<?>[] snapshot = pending.toArray(CompletableFuture[]::new);
        if (snapshot.length == 0) {
            return;
        }
        try {
            CompletableFuture.allOf(snapshot).get(timeout.toMillis(), TimeUnit.MILLISECONDS);
        } catch (TimeoutException exc) {
            throw new LsmsgException("Timed out waiting for pending handlers", exc);
        } catch (Exception exc) {
            throw new LsmsgException("Failed while draining pending handlers", exc);
        }
    }

    RunResult invoke(Event event, String agent, InvokeOptions options) {
        LangGraphClient client = requireLangGraph();
        RunRequest request = runRequestFor(event, agent, options);
        String runId = client.createRun(request);
        return client.waitRun(request.threadId(), runId);
    }

    List<RunChunk> stream(Event event, String agent, InvokeOptions options) {
        return requireLangGraph().stream(runRequestFor(event, agent, options));
    }

    SentMessage sendMessage(Event event, String text) {
        LOGGER.log(
                System.Logger.Level.INFO,
                "Reply to {0}/{1}: {2}",
                event.platform().name().wireName(),
                event.channelId(),
                text);
        return new SentMessage("pending", event.platform().name(), event.channelId());
    }

    @Override
    public void close() {
        executor.shutdown();
    }

    private long register(RegisteredHandler handler) {
        long id = nextHandlerId.getAndIncrement();
        handlers.put(id, handler);
        return id;
    }

    private void scheduleDispatch(Event event) {
        CompletableFuture<Void> future = CompletableFuture.runAsync(() -> dispatch(event), executor);
        pending.add(future);
        future.whenComplete((ignored, error) -> pending.remove(future));
    }

    private RunRequest runRequestFor(Event event, String agent, InvokeOptions options) {
        Object input = options == null ? null : options.input();
        if (input == null) {
            input = Map.of("messages", List.of(Map.of("role", "user", "content", event.text())));
        }
        RunRequest.Builder builder = RunRequest.builder()
                .agent(agent)
                .threadId(event.internalThreadId())
                .input(input);
        if (options != null && options.config() != null) {
            builder.config(options.config());
        }
        if (options != null && options.metadata() != null) {
            builder.metadata(options.metadata());
        }
        return builder.build();
    }

    private LangGraphClient requireLangGraph() {
        if (langGraphClient == null) {
            throw new IllegalStateException("LangGraph is not configured for this Bot");
        }
        return langGraphClient;
    }

    private static String header(Map<String, String> headers, String name) {
        if (headers == null) {
            return "";
        }
        String direct = headers.get(name);
        if (direct != null) {
            return direct;
        }
        for (Map.Entry<String, String> entry : headers.entrySet()) {
            if (entry.getKey() != null && entry.getKey().equalsIgnoreCase(name)) {
                return entry.getValue();
            }
        }
        return "";
    }

    private record RegisteredHandler(
            EventKind kind,
            String command,
            String emoji,
            String rawEventType,
            HandlerOptions options,
            EventHandler callback) {
        private boolean matches(Event event) {
            if (kind != null && event.kind() != kind) {
                return false;
            }
            if (command != null && !command.equals(event.command())) {
                return false;
            }
            if (emoji != null && !emoji.equals(event.emoji())) {
                return false;
            }
            if (rawEventType != null && !rawEventType.equals(event.rawEventType())) {
                return false;
            }
            if (options == null) {
                return true;
            }
            if (options.platform() != null && options.platform() != event.platform().name()) {
                return false;
            }
            return options.pattern() == null || options.pattern().matcher(event.text()).find();
        }
    }
}
