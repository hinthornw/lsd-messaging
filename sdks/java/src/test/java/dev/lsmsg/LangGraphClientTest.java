package dev.lsmsg;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

final class LangGraphClientTest {
    void createsWaitsAndStreamsRuns() throws Exception {
        AtomicReference<String> createBody = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/threads/thread-123/runs", exchange -> {
            if ("POST".equals(exchange.getRequestMethod())) {
                createBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                writeJson(exchange, 200, "{\"run_id\":\"run-123\"}");
                return;
            }
            writeJson(exchange, 404, "{\"error\":\"not found\"}");
        });
        server.createContext("/threads/thread-123/runs/run-123/join", exchange -> writeJson(exchange, 200, "{\"messages\":[{\"content\":\"done\"}]}"));
        server.createContext("/threads/thread-123/runs/stream", exchange -> {
            String body = String.join(
                    "\n",
                    "event: messages/partial",
                    "data: [{\"content\":\"hel\"}]",
                    "",
                    "event: messages/partial",
                    "data: [{\"content\":\"lo\"}]",
                    "",
                    "event: run/completed",
                    "data: {\"status\":\"completed\"}",
                    "",
                    "");
            exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
            exchange.sendResponseHeaders(200, body.getBytes(StandardCharsets.UTF_8).length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(body.getBytes(StandardCharsets.UTF_8));
            }
        });
        server.start();

        try {
            String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
            LangGraphClient client = new LangGraphClient(baseUrl, "secret");
            RunRequest request = RunRequest.builder()
                    .agent("assistant")
                    .threadId("thread-123")
                    .input(Map.of("messages", List.of(Map.of("role", "user", "content", "hi"))))
                    .build();

            String runId = client.createRun(request);
            TestSupport.assertEquals("run-123", runId, "createRun should return run id");
            TestSupport.assertTrue(createBody.get().contains("\"assistant_id\":\"assistant\""), "assistant_id should be present");

            RunResult result = client.waitRun("thread-123", runId);
            TestSupport.assertEquals("done", result.text(), "waitRun should extract text");

            List<RunChunk> chunks = client.stream(request);
            TestSupport.assertEquals(3, chunks.size(), "expected all SSE chunks");
            TestSupport.assertEquals("hello", chunks.get(1).text(), "stream should accumulate text");
            TestSupport.assertEquals("lo", chunks.get(1).textDelta(), "second delta should be preserved");
        } finally {
            server.stop(0);
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

