package dev.lsmsg;

public final class BotConfig {
    private final SlackConfig slack;
    private final TeamsConfig teams;
    private final LangGraphConfig langGraph;

    private BotConfig(Builder builder) {
        this.slack = builder.slack;
        this.teams = builder.teams;
        this.langGraph = builder.langGraph;
    }

    public SlackConfig slack() {
        return slack;
    }

    public TeamsConfig teams() {
        return teams;
    }

    public LangGraphConfig langGraph() {
        return langGraph;
    }

    public static Builder builder() {
        return new Builder();
    }

    public record SlackConfig(String signingSecret, String botToken) {}

    public record TeamsConfig(String appId, String appPassword) {}

    public record LangGraphConfig(String url, String apiKey) {}

    public static final class Builder {
        private SlackConfig slack;
        private TeamsConfig teams;
        private LangGraphConfig langGraph;

        public Builder slack(String signingSecret, String botToken) {
            this.slack = new SlackConfig(signingSecret, botToken);
            return this;
        }

        public Builder teams(String appId, String appPassword) {
            this.teams = new TeamsConfig(appId, appPassword);
            return this;
        }

        public Builder langGraph(String url, String apiKey) {
            this.langGraph = new LangGraphConfig(url, apiKey);
            return this;
        }

        public BotConfig build() {
            return new BotConfig(this);
        }
    }
}

