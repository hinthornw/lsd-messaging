package dev.lsmsg;

import java.util.regex.Pattern;

public final class HandlerOptions {
    private final Pattern pattern;
    private final Platform platform;

    private HandlerOptions(Builder builder) {
        this.pattern = builder.pattern;
        this.platform = builder.platform;
    }

    public Pattern pattern() {
        return pattern;
    }

    public Platform platform() {
        return platform;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private Pattern pattern;
        private Platform platform;

        public Builder pattern(String regex) {
            this.pattern = Pattern.compile(regex);
            return this;
        }

        public Builder pattern(Pattern value) {
            this.pattern = value;
            return this;
        }

        public Builder platform(Platform value) {
            this.platform = value;
            return this;
        }

        public HandlerOptions build() {
            return new HandlerOptions(this);
        }
    }
}

