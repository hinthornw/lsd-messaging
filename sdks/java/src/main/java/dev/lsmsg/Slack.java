package dev.lsmsg;

import java.nio.charset.StandardCharsets;
import java.util.Map;

public final class Slack {
    private Slack() {}

    public static boolean verifySignature(String signingSecret, String timestamp, String signature, byte[] body) {
        return NativeBridge.instance().verifySlackSignature(signingSecret, timestamp, signature, body);
    }

    public static SlackWebhookResult parseWebhook(byte[] body, String contentType) {
        Map<String, Object> parsed = Json.parseObject(NativeBridge.instance().parseSlackWebhook(body, contentType));
        String type = String.valueOf(parsed.get("type"));
        return switch (type) {
            case "challenge" -> new SlackWebhookResult.Challenge(String.valueOf(parsed.get("challenge")));
            case "event" -> {
                @SuppressWarnings("unchecked")
                Map<String, Object> event = (Map<String, Object>) parsed.get("event");
                yield new SlackWebhookResult.EventPayload(Event.fromMap(event, null));
            }
            case "ignored" -> SlackWebhookResult.Ignored.INSTANCE;
            default -> throw new LsmsgException("Unexpected Slack webhook result: " + type);
        };
    }

    public static SlackWebhookResult parseWebhook(String body, String contentType) {
        return parseWebhook(body.getBytes(StandardCharsets.UTF_8), contentType);
    }

    public static String stripMentions(String text) {
        return NativeBridge.instance().stripSlackMentions(text);
    }
}
