package dev.lsmsg;

public final class ThreadIds {
    private ThreadIds() {}

    public static String deterministic(Platform platform, String workspaceId, String channelId, String threadId) {
        return NativeBridge.instance().deterministicThreadId(platform.wireName(), workspaceId, channelId, threadId);
    }
}

