package dev.lsmsg;

public final class InvokeOptions {
    private final Object input;
    private final Object config;
    private final Object metadata;

    private InvokeOptions(Builder builder) {
        this.input = builder.input;
        this.config = builder.config;
        this.metadata = builder.metadata;
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
        private Object input;
        private Object config;
        private Object metadata;

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

        public InvokeOptions build() {
            return new InvokeOptions(this);
        }
    }
}

