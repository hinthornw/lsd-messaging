package dev.lsmsg;

public enum EventKind {
    MESSAGE("message"),
    MENTION("mention"),
    COMMAND("command"),
    REACTION("reaction"),
    RAW("raw");

    private final String wireName;

    EventKind(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }

    public static EventKind fromWireName(String value) {
        for (EventKind kind : values()) {
            if (kind.wireName.equals(value)) {
                return kind;
            }
        }
        throw new LsmsgException("Unknown event kind: " + value);
    }
}

