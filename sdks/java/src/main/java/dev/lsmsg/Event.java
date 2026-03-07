package dev.lsmsg;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Event {
    private final EventKind kind;
    private final PlatformCapabilities platform;
    private final String workspaceId;
    private final String channelId;
    private final String threadId;
    private final String messageId;
    private final UserInfo user;
    private final String text;
    private final String command;
    private final String emoji;
    private final String rawEventType;
    private final Object raw;
    private final Bot bot;

    Event(
            EventKind kind,
            PlatformCapabilities platform,
            String workspaceId,
            String channelId,
            String threadId,
            String messageId,
            UserInfo user,
            String text,
            String command,
            String emoji,
            String rawEventType,
            Object raw,
            Bot bot) {
        this.kind = kind;
        this.platform = platform;
        this.workspaceId = workspaceId;
        this.channelId = channelId;
        this.threadId = threadId;
        this.messageId = messageId;
        this.user = user;
        this.text = text;
        this.command = command;
        this.emoji = emoji;
        this.rawEventType = rawEventType;
        this.raw = raw;
        this.bot = bot;
    }

    public EventKind kind() {
        return kind;
    }

    public PlatformCapabilities platform() {
        return platform;
    }

    public String workspaceId() {
        return workspaceId;
    }

    public String channelId() {
        return channelId;
    }

    public String threadId() {
        return threadId;
    }

    public String messageId() {
        return messageId;
    }

    public UserInfo user() {
        return user;
    }

    public String text() {
        return text;
    }

    public String command() {
        return command;
    }

    public String emoji() {
        return emoji;
    }

    public String rawEventType() {
        return rawEventType;
    }

    public Object raw() {
        return raw;
    }

    public String internalThreadId() {
        return ThreadIds.deterministic(platform.name(), workspaceId, channelId, threadId);
    }

    public SentMessage reply(String text) {
        return requireBot().sendMessage(this, text);
    }

    public RunResult invoke(String agent) {
        return invoke(agent, null);
    }

    public RunResult invoke(String agent, InvokeOptions options) {
        return requireBot().invoke(this, agent, options);
    }

    public List<RunChunk> stream(String agent) {
        return stream(agent, null);
    }

    public List<RunChunk> stream(String agent, InvokeOptions options) {
        return requireBot().stream(this, agent, options);
    }

    Event bind(Bot value) {
        if (bot == value) {
            return this;
        }
        return new Event(
                kind,
                platform,
                workspaceId,
                channelId,
                threadId,
                messageId,
                user,
                text,
                command,
                emoji,
                rawEventType,
                raw,
                value);
    }

    Map<String, Object> toMap() {
        LinkedHashMap<String, Object> out = new LinkedHashMap<>();
        LinkedHashMap<String, Object> platformMap = new LinkedHashMap<>();
        platformMap.put("name", platform.name().wireName());
        platformMap.put("ephemeral", platform.ephemeral());
        platformMap.put("threads", platform.threads());
        platformMap.put("reactions", platform.reactions());
        platformMap.put("streaming", platform.streaming());
        platformMap.put("modals", platform.modals());
        platformMap.put("typing_indicator", platform.typingIndicator());

        LinkedHashMap<String, Object> userMap = new LinkedHashMap<>();
        userMap.put("id", user.id());
        userMap.put("name", user.name());
        userMap.put("email", user.email());

        out.put("kind", kind.wireName());
        out.put("platform", platformMap);
        out.put("workspace_id", workspaceId);
        out.put("channel_id", channelId);
        out.put("thread_id", threadId);
        out.put("message_id", messageId);
        out.put("user", userMap);
        out.put("text", text);
        out.put("command", command);
        out.put("emoji", emoji);
        out.put("raw_event_type", rawEventType);
        out.put("raw", raw);
        return out;
    }

    @SuppressWarnings("unchecked")
    static Event fromMap(Map<String, Object> data, Bot bot) {
        Map<String, Object> platformData = asObject(data.get("platform"));
        Map<String, Object> userData = asObject(data.get("user"));
        return new Event(
                EventKind.fromWireName(asString(data.get("kind"))),
                new PlatformCapabilities(
                        Platform.fromWireName(asString(platformData.get("name"))),
                        asBoolean(platformData.get("ephemeral")),
                        asBoolean(platformData.get("threads")),
                        asBoolean(platformData.get("reactions")),
                        asBoolean(platformData.get("streaming")),
                        asBoolean(platformData.get("modals")),
                        asBoolean(platformData.get("typing_indicator"))),
                asString(data.get("workspace_id")),
                asString(data.get("channel_id")),
                asString(data.get("thread_id")),
                asString(data.get("message_id")),
                new UserInfo(
                        asString(userData.get("id")),
                        nullableString(userData.get("name")),
                        nullableString(userData.get("email"))),
                asString(data.getOrDefault("text", "")),
                nullableString(data.get("command")),
                nullableString(data.get("emoji")),
                nullableString(data.get("raw_event_type")),
                data.get("raw"),
                bot);
    }

    private Bot requireBot() {
        if (bot == null) {
            throw new IllegalStateException("Event is not associated with a Bot instance");
        }
        return bot;
    }

    private static Map<String, Object> asObject(Object value) {
        if (value instanceof Map<?, ?> map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> cast = (Map<String, Object>) map;
            return cast;
        }
        return Map.of();
    }

    private static String asString(Object value) {
        if (value == null) {
            return "";
        }
        return String.valueOf(value);
    }

    private static String nullableString(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private static boolean asBoolean(Object value) {
        return value instanceof Boolean bool ? bool : Boolean.parseBoolean(String.valueOf(value));
    }
}
