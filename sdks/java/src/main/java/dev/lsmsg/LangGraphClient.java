package dev.lsmsg;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class LangGraphClient {
    private final HttpClient httpClient;
    private final String baseUrl;
    private final String apiKey;

    public LangGraphClient(String baseUrl, String apiKey) {
        this(HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build(), baseUrl, apiKey);
    }

    LangGraphClient(HttpClient httpClient, String baseUrl, String apiKey) {
        this.httpClient = httpClient;
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.apiKey = apiKey;
    }

    public String createRun(RunRequest request) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("assistant_id", request.agent());
        payload.put("if_not_exists", "create");
        if (request.input() != null) {
            payload.put("input", request.input());
        }
        if (request.config() != null) {
            payload.put("config", request.config());
        }
        if (request.metadata() != null) {
            payload.put("metadata", request.metadata());
        }

        HttpRequest httpRequest = requestBuilder("/threads/" + request.threadId() + "/runs")
                .POST(HttpRequest.BodyPublishers.ofString(Json.stringify(payload)))
                .build();
        Map<String, Object> response = sendJson(httpRequest);
        Object runId = response.get("run_id");
        if (!(runId instanceof String value) || value.isBlank()) {
            throw new LsmsgException("LangGraph response missing run_id");
        }
        return value;
    }

    public RunResult waitRun(String threadId, String runId) {
        HttpRequest httpRequest = requestBuilder("/threads/" + threadId + "/runs/" + runId + "/join")
                .GET()
                .build();
        Object output = sendJson(httpRequest);
        return new RunResult(runId, "completed", output);
    }

    public List<RunChunk> stream(RunRequest request) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("assistant_id", request.agent());
        payload.put("stream_mode", "messages");
        payload.put("if_not_exists", "create");
        if (request.input() != null) {
            payload.put("input", request.input());
        }
        if (request.config() != null) {
            payload.put("config", request.config());
        }
        if (request.metadata() != null) {
            payload.put("metadata", request.metadata());
        }

        HttpRequest httpRequest = requestBuilder("/threads/" + request.threadId() + "/runs/stream")
                .POST(HttpRequest.BodyPublishers.ofString(Json.stringify(payload)))
                .build();
        try {
            HttpResponse<java.io.InputStream> response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofInputStream());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                String body = new String(response.body().readAllBytes(), StandardCharsets.UTF_8);
                ensureSuccess(response.statusCode(), body);
            }
            List<RunChunk> chunks = new ArrayList<>();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
                String line;
                String currentEvent = "";
                List<String> dataLines = new ArrayList<>();
                StringBuilder accumulated = new StringBuilder();
                while ((line = reader.readLine()) != null) {
                    if (line.isEmpty()) {
                        emitChunk(chunks, currentEvent, dataLines, accumulated);
                        dataLines.clear();
                        currentEvent = "";
                        continue;
                    }
                    if (line.startsWith("event:")) {
                        currentEvent = line.substring(6).trim();
                        continue;
                    }
                    if (line.startsWith("data:")) {
                        dataLines.add(line.substring(5).trim());
                    }
                }
                emitChunk(chunks, currentEvent, dataLines, accumulated);
                return chunks;
            }
        } catch (IOException exc) {
            throw new LsmsgException("LangGraph stream failed", exc);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            throw new LsmsgException("LangGraph stream interrupted", exc);
        }
    }

    public void cancelRun(String threadId, String runId) {
        HttpRequest httpRequest = requestBuilder("/threads/" + threadId + "/runs/" + runId + "/cancel")
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
        try {
            HttpResponse<String> response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            ensureSuccess(response.statusCode(), response.body());
        } catch (IOException exc) {
            throw new LsmsgException("LangGraph cancel failed", exc);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            throw new LsmsgException("LangGraph cancel interrupted", exc);
        }
    }

    private HttpRequest.Builder requestBuilder(String path) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .header("Content-Type", "application/json");
        if (apiKey != null && !apiKey.isBlank()) {
            builder.header("Authorization", "Bearer " + apiKey);
        }
        return builder;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> sendJson(HttpRequest request) {
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            ensureSuccess(response.statusCode(), response.body());
            Object parsed = Json.parse(response.body());
            if (parsed instanceof Map<?, ?> map) {
                return (Map<String, Object>) map;
            }
            throw new LsmsgException("Expected JSON object from LangGraph");
        } catch (IOException exc) {
            throw new LsmsgException("LangGraph request failed", exc);
        } catch (InterruptedException exc) {
            Thread.currentThread().interrupt();
            throw new LsmsgException("LangGraph request interrupted", exc);
        }
    }

    private void emitChunk(List<RunChunk> chunks, String currentEvent, List<String> dataLines, StringBuilder accumulated) {
        if (dataLines.isEmpty()) {
            return;
        }
        String payload = String.join("\n", dataLines);
        if ("[DONE]".equals(payload)) {
            return;
        }
        Object parsed = Json.parse(payload);
        String delta = extractTextDelta(currentEvent, parsed);
        accumulated.append(delta);
        chunks.add(new RunChunk(currentEvent, accumulated.toString(), delta, parsed));
    }

    private static String extractTextDelta(String currentEvent, Object parsed) {
        if (!"messages/partial".equals(currentEvent)) {
            return "";
        }
        if (parsed instanceof Map<?, ?> map) {
            Object content = map.get("content");
            return content instanceof String text ? text : "";
        }
        if (parsed instanceof List<?> list && !list.isEmpty()) {
            Object last = list.get(list.size() - 1);
            if (last instanceof Map<?, ?> map) {
                Object content = map.get("content");
                return content instanceof String text ? text : "";
            }
        }
        return "";
    }

    private static void ensureSuccess(int statusCode, String body) {
        if (statusCode >= 200 && statusCode < 300) {
            return;
        }
        throw new LsmsgException("LangGraph API error " + statusCode + ": " + body);
    }
}
