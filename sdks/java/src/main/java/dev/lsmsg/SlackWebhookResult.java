package dev.lsmsg;

public sealed interface SlackWebhookResult permits SlackWebhookResult.Challenge, SlackWebhookResult.EventPayload, SlackWebhookResult.Ignored {
    record Challenge(String value) implements SlackWebhookResult {}

    record EventPayload(Event event) implements SlackWebhookResult {}

    final class Ignored implements SlackWebhookResult {
        public static final Ignored INSTANCE = new Ignored();

        private Ignored() {}
    }
}
