package dev.lsmsg;

import java.nio.charset.StandardCharsets;
import java.util.Map;

public record WebhookResponse(int statusCode, String contentType, byte[] body) {
    public static WebhookResponse json(int statusCode, Map<String, Object> payload) {
        return new WebhookResponse(
                statusCode,
                "application/json",
                Json.stringify(payload).getBytes(StandardCharsets.UTF_8));
    }

    public String bodyText() {
        return new String(body, StandardCharsets.UTF_8);
    }
}

