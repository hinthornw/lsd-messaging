package dev.lsmsg;

public final class RunRequest {
    private final String agent;
    private final String threadId;
    private final Object input;
    private final Object config;
    private final Object metadata;

    private RunRequest(Builder builder) {
        this.agent = builder.agent;
        this.threadId = builder.threadId;
        this.input = builder.input;
        this.config = builder.config;
        this.metadata = builder.metadata;
    }

    public String agent() {
        return agent;
    }

    public String threadId() {
        return threadId;
    }

    public Object input() {
        return input;
    }

    public Object config() {
        return config;
    }

    public Object metadata() {
        return metadata;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String agent;
        private String threadId;
        private Object input;
        private Object config;
        private Object metadata;

        public Builder agent(String value) {
            this.agent = value;
            return this;
        }

        public Builder threadId(String value) {
            this.threadId = value;
            return this;
        }

        public Builder input(Object value) {
            this.input = value;
            return this;
        }

        public Builder config(Object value) {
            this.config = value;
            return this;
        }

        public Builder metadata(Object value) {
            this.metadata = value;
            return this;
        }

        public RunRequest build() {
            if (agent == null || agent.isBlank()) {
                throw new LsmsgException("RunRequest.agent is required");
            }
            if (threadId == null || threadId.isBlank()) {
                throw new LsmsgException("RunRequest.threadId is required");
            }
            return new RunRequest(this);
        }
    }
}

