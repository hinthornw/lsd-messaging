package dev.lsmsg;

public record PlatformCapabilities(
        Platform name,
        boolean ephemeral,
        boolean threads,
        boolean reactions,
        boolean streaming,
        boolean modals,
        boolean typingIndicator) {}

