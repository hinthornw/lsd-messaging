package dev.lsmsg;

public enum Platform {
    SLACK("slack"),
    TEAMS("teams"),
    DISCORD("discord"),
    TELEGRAM("telegram"),
    GITHUB("github"),
    LINEAR("linear"),
    GCHAT("gchat"),
    UNKNOWN("unknown");

    private final String wireName;

    Platform(String wireName) {
        this.wireName = wireName;
    }

    public String wireName() {
        return wireName;
    }

    public static Platform fromWireName(String value) {
        for (Platform platform : values()) {
            if (platform.wireName.equals(value)) {
                return platform;
            }
        }
        return UNKNOWN;
    }
}

