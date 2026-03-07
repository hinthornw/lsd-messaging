package dev.lsmsg;

import java.nio.charset.StandardCharsets;
import java.util.Map;

final class NativeBridgeTest {
    void parsesSlackUrlVerificationChallenge() {
        byte[] body = "{\"type\":\"url_verification\",\"challenge\":\"abc123\"}".getBytes(StandardCharsets.UTF_8);
        SlackWebhookResult result = Slack.parseWebhook(body, "application/json");
        TestSupport.assertTrue(result instanceof SlackWebhookResult.Challenge, "expected challenge result");
        SlackWebhookResult.Challenge challenge = (SlackWebhookResult.Challenge) result;
        TestSupport.assertEquals("abc123", challenge.value(), "challenge should round-trip");
    }

    void parsesTeamsMessageEvent() {
        Event event = Teams.parseWebhook(Map.of(
                "type", "message",
                "text", "hello teams",
                "from", Map.of("id", "U1", "name", "Alice"),
                "conversation", Map.of("id", "conv-1", "tenantId", "tenant-1"),
                "channelData", Map.of(
                        "tenant", Map.of("id", "tenant-1"),
                        "team", Map.of("id", "team-1")),
                "id", "msg-1"));
        TestSupport.assertNotNull(event, "expected Teams event");
        TestSupport.assertEquals(EventKind.MESSAGE, event.kind(), "expected message event");
        TestSupport.assertEquals("hello teams", event.text(), "text should round-trip");
    }

    void computesDeterministicThreadIds() {
        String first = ThreadIds.deterministic(Platform.SLACK, "T1", "C1", "thread-1");
        String second = ThreadIds.deterministic(Platform.SLACK, "T1", "C1", "thread-1");
        TestSupport.assertEquals(first, second, "thread ids should be stable");
    }
}

