package dev.lsmsg;

import java.util.Map;

public final class Teams {
    private Teams() {}

    public static Event parseWebhook(String payloadJson) {
        String raw = NativeBridge.instance().parseTeamsWebhook(payloadJson);
        if (raw == null || raw.equals("null")) {
            return null;
        }
        Map<String, Object> parsed = Json.parseObject(raw);
        return Event.fromMap(parsed, null);
    }

    public static Event parseWebhook(Map<String, Object> payload) {
        return parseWebhook(Json.stringify(payload));
    }

    public static String stripMentions(String text) {
        return NativeBridge.instance().stripTeamsMentions(text);
    }
}

