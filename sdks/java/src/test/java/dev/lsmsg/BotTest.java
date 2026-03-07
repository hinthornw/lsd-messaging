package dev.lsmsg;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

final class BotTest {
    void rejectsMissingSlackSignatureHeaders() {
        try (Bot bot = new Bot(BotConfig.builder().slack("secret", "xoxb-token").build())) {
            WebhookResponse response = bot.handleSlackWebhook(
                    "{\"type\":\"url_verification\",\"challenge\":\"abc123\"}".getBytes(StandardCharsets.UTF_8),
                    "application/json",
                    Map.of());
            TestSupport.assertEquals(401, response.statusCode(), "missing signature headers should be rejected");
        }
    }

    void dispatchesSlackMentionHandlers() {
        try (Bot bot = new Bot(BotConfig.builder().slack("secret", "xoxb-token").build())) {
            List<String> handled = new ArrayList<>();
            bot.onMention(event -> handled.add(event.text()));

            byte[] body = Json.stringify(Map.of(
                            "type", "event_callback",
                            "team_id", "T1",
                            "event", Map.of(
                                    "type", "app_mention",
                                    "text", "<@U_BOT> hello",
                                    "channel", "C1",
                                    "ts", "123.456",
                                    "user", "U1")))
                    .getBytes(StandardCharsets.UTF_8);
            WebhookResponse response = bot.handleSlackWebhook(body, "application/json", signedHeaders("secret", body));
            TestSupport.assertEquals(200, response.statusCode(), "webhook should be acknowledged");

            bot.drain(Duration.ofSeconds(2));
            TestSupport.assertEquals(List.of("hello"), handled, "mention text should be normalized");
        }
    }

    void invokesLangGraphFromHandlers() throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/threads/", this::handleLangGraphRequest);
        server.start();

        try (Bot bot = new Bot(BotConfig.builder()
                .slack("secret", "xoxb-token")
                .langGraph("http://127.0.0.1:" + server.getAddress().getPort(), null)
                .build())) {
            List<String> outputs = new ArrayList<>();
            bot.onMention(event -> outputs.add(event.invoke("assistant").text()));

            byte[] body = Json.stringify(Map.of(
                            "type", "event_callback",
                            "team_id", "T1",
                            "event", Map.of(
                                    "type", "app_mention",
                                    "text", "<@U_BOT> run it",
                                    "channel", "C1",
                                    "ts", "999.111",
                                    "user", "U1")))
                    .getBytes(StandardCharsets.UTF_8);
            bot.handleSlackWebhook(body, "application/json", signedHeaders("secret", body));
            bot.drain(Duration.ofSeconds(2));

            TestSupport.assertEquals(List.of("done from lg"), outputs, "handler should receive LangGraph result");
        } finally {
            server.stop(0);
        }
    }

    private void handleLangGraphRequest(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();
        if (path.endsWith("/runs") && "POST".equals(exchange.getRequestMethod())) {
            writeJson(exchange, 200, "{\"run_id\":\"run-123\"}");
            return;
        }
        if (path.endsWith("/join") && "GET".equals(exchange.getRequestMethod())) {
            writeJson(exchange, 200, "{\"messages\":[{\"content\":\"done from lg\"}]}");
            return;
        }
        writeJson(exchange, 404, "{\"error\":\"not found\"}");
    }

    private static Map<String, String> signedHeaders(String signingSecret, byte[] body) {
        try {
            String timestamp = Long.toString(Instant.now().getEpochSecond());
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(signingSecret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            mac.update("v0:".getBytes(StandardCharsets.UTF_8));
            mac.update(timestamp.getBytes(StandardCharsets.UTF_8));
            mac.update(":".getBytes(StandardCharsets.UTF_8));
            mac.update(body);
            byte[] digest = mac.doFinal();

            StringBuilder hex = new StringBuilder();
            for (byte b : digest) {
                hex.append(String.format("%02x", b));
            }

            HashMap<String, String> headers = new HashMap<>();
            headers.put("x-slack-request-timestamp", timestamp);
            headers.put("x-slack-signature", "v0=" + hex);
            return headers;
        } catch (Exception exc) {
            throw new RuntimeException(exc);
        }
    }

    private static void writeJson(HttpExchange exchange, int statusCode, String body) throws IOException {
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(statusCode, body.getBytes(StandardCharsets.UTF_8).length);
        try (OutputStream stream = exchange.getResponseBody()) {
            stream.write(body.getBytes(StandardCharsets.UTF_8));
        }
    }
}

