package dev.lsmsg;

import java.util.List;
import java.util.Map;

public record RunResult(String id, String status, Object output) {
    public String text() {
        if (!(output instanceof Map<?, ?> map)) {
            return "";
        }
        Object messages = map.get("messages");
        if (!(messages instanceof List<?> list) || list.isEmpty()) {
            return "";
        }
        Object last = list.get(list.size() - 1);
        if (!(last instanceof Map<?, ?> message)) {
            return "";
        }
        Object content = message.get("content");
        return content instanceof String text ? text : "";
    }
}

