package dev.lsmsg;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class Json {
    private Json() {}

    static Object parse(String source) {
        Parser parser = new Parser(source);
        Object value = parser.parseValue();
        parser.skipWhitespace();
        if (!parser.isDone()) {
            throw new LsmsgException("Unexpected trailing JSON content");
        }
        return value;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> parseObject(String source) {
        Object value = parse(source);
        if (!(value instanceof Map<?, ?> map)) {
            throw new LsmsgException("Expected JSON object");
        }
        return (Map<String, Object>) map;
    }

    static String stringify(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(out, value);
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    private static void writeValue(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
            return;
        }
        if (value instanceof String text) {
            out.append('"');
            for (int i = 0; i < text.length(); i++) {
                char ch = text.charAt(i);
                switch (ch) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\b' -> out.append("\\b");
                    case '\f' -> out.append("\\f");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (ch < 0x20) {
                            out.append(String.format("\\u%04x", (int) ch));
                        } else {
                            out.append(ch);
                        }
                    }
                }
            }
            out.append('"');
            return;
        }
        if (value instanceof Number || value instanceof Boolean) {
            out.append(value);
            return;
        }
        if (value instanceof Map<?, ?> map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(out, String.valueOf(entry.getKey()));
                out.append(':');
                writeValue(out, entry.getValue());
            }
            out.append('}');
            return;
        }
        if (value instanceof Iterable<?> iterable) {
            out.append('[');
            boolean first = true;
            for (Object item : iterable) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(out, item);
            }
            out.append(']');
            return;
        }
        if (value.getClass().isArray() && value instanceof Object[] array) {
            out.append('[');
            for (int i = 0; i < array.length; i++) {
                if (i > 0) {
                    out.append(',');
                }
                writeValue(out, array[i]);
            }
            out.append(']');
            return;
        }
        throw new LsmsgException("Unsupported JSON value type: " + value.getClass().getName());
    }

    private static final class Parser {
        private final String source;
        private int index;

        private Parser(String source) {
            this.source = source;
        }

        private Object parseValue() {
            skipWhitespace();
            if (isDone()) {
                throw new LsmsgException("Unexpected end of JSON");
            }
            char ch = source.charAt(index);
            return switch (ch) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't' -> parseLiteral("true", Boolean.TRUE);
                case 'f' -> parseLiteral("false", Boolean.FALSE);
                case 'n' -> parseLiteral("null", null);
                default -> parseNumber();
            };
        }

        private Map<String, Object> parseObject() {
            index++;
            LinkedHashMap<String, Object> map = new LinkedHashMap<>();
            skipWhitespace();
            if (peek('}')) {
                index++;
                return map;
            }
            while (true) {
                skipWhitespace();
                String key = parseString();
                skipWhitespace();
                expect(':');
                Object value = parseValue();
                map.put(key, value);
                skipWhitespace();
                if (peek('}')) {
                    index++;
                    return map;
                }
                expect(',');
            }
        }

        private List<Object> parseArray() {
            index++;
            ArrayList<Object> list = new ArrayList<>();
            skipWhitespace();
            if (peek(']')) {
                index++;
                return list;
            }
            while (true) {
                list.add(parseValue());
                skipWhitespace();
                if (peek(']')) {
                    index++;
                    return list;
                }
                expect(',');
            }
        }

        private String parseString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (!isDone()) {
                char ch = source.charAt(index++);
                if (ch == '"') {
                    return out.toString();
                }
                if (ch != '\\') {
                    out.append(ch);
                    continue;
                }
                if (isDone()) {
                    throw new LsmsgException("Incomplete JSON escape");
                }
                char escaped = source.charAt(index++);
                switch (escaped) {
                    case '"', '\\', '/' -> out.append(escaped);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (index + 4 > source.length()) {
                            throw new LsmsgException("Invalid unicode escape");
                        }
                        String hex = source.substring(index, index + 4);
                        out.append((char) Integer.parseInt(hex, 16));
                        index += 4;
                    }
                    default -> throw new LsmsgException("Invalid JSON escape: \\" + escaped);
                }
            }
            throw new LsmsgException("Unterminated JSON string");
        }

        private Object parseLiteral(String literal, Object value) {
            if (!source.startsWith(literal, index)) {
                throw new LsmsgException("Invalid JSON token");
            }
            index += literal.length();
            return value;
        }

        private Number parseNumber() {
            int start = index;
            if (peek('-')) {
                index++;
            }
            while (!isDone() && Character.isDigit(source.charAt(index))) {
                index++;
            }
            boolean fractional = false;
            if (!isDone() && source.charAt(index) == '.') {
                fractional = true;
                index++;
                while (!isDone() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            if (!isDone() && (source.charAt(index) == 'e' || source.charAt(index) == 'E')) {
                fractional = true;
                index++;
                if (!isDone() && (source.charAt(index) == '+' || source.charAt(index) == '-')) {
                    index++;
                }
                while (!isDone() && Character.isDigit(source.charAt(index))) {
                    index++;
                }
            }
            String token = source.substring(start, index);
            try {
                return fractional ? Double.parseDouble(token) : Long.parseLong(token);
            } catch (NumberFormatException exc) {
                throw new LsmsgException("Invalid JSON number: " + token, exc);
            }
        }

        private void expect(char expected) {
            skipWhitespace();
            if (isDone() || source.charAt(index) != expected) {
                throw new LsmsgException("Expected '" + expected + "'");
            }
            index++;
        }

        private boolean peek(char expected) {
            return !isDone() && source.charAt(index) == expected;
        }

        private void skipWhitespace() {
            while (!isDone() && Character.isWhitespace(source.charAt(index))) {
                index++;
            }
        }

        private boolean isDone() {
            return index >= source.length();
        }
    }
}

