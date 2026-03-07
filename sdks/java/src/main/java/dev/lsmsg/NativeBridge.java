package dev.lsmsg;

import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.MethodHandle;
import java.nio.file.Files;
import java.nio.file.Path;

final class NativeBridge {
    private static final NativeBridge INSTANCE = new NativeBridge();

    private final MethodHandle freeString;
    private final MethodHandle slackVerifySignature;
    private final MethodHandle slackParseWebhook;
    private final MethodHandle slackStripMentions;
    private final MethodHandle teamsParseWebhook;
    private final MethodHandle teamsStripMentions;
    private final MethodHandle deterministicThreadId;

    private NativeBridge() {
        Linker linker = Linker.nativeLinker();
        SymbolLookup lookup = SymbolLookup.libraryLookup(resolveLibraryPath(), Arena.global());

        this.freeString = downcall(linker, lookup, "lsmsg_free_string", FunctionDescriptor.ofVoid(ValueLayout.ADDRESS));
        this.slackVerifySignature = downcall(
                linker,
                lookup,
                "lsmsg_slack_verify_signature",
                FunctionDescriptor.of(
                        ValueLayout.JAVA_INT,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.JAVA_LONG));
        this.slackParseWebhook = downcall(
                linker,
                lookup,
                "lsmsg_slack_parse_webhook",
                FunctionDescriptor.of(
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.JAVA_LONG,
                        ValueLayout.ADDRESS));
        this.slackStripMentions = downcall(
                linker,
                lookup,
                "lsmsg_slack_strip_mentions",
                FunctionDescriptor.of(ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        this.teamsParseWebhook = downcall(
                linker,
                lookup,
                "lsmsg_teams_parse_webhook",
                FunctionDescriptor.of(ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        this.teamsStripMentions = downcall(
                linker,
                lookup,
                "lsmsg_teams_strip_mentions",
                FunctionDescriptor.of(ValueLayout.ADDRESS, ValueLayout.ADDRESS));
        this.deterministicThreadId = downcall(
                linker,
                lookup,
                "lsmsg_deterministic_thread_id",
                FunctionDescriptor.of(
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS,
                        ValueLayout.ADDRESS));
    }

    static NativeBridge instance() {
        return INSTANCE;
    }

    boolean verifySlackSignature(String signingSecret, String timestamp, String signature, byte[] body) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment nativeBody = nativeBytes(arena, body);
            int result = (int) slackVerifySignature.invoke(
                    arena.allocateUtf8String(signingSecret),
                    arena.allocateUtf8String(timestamp),
                    arena.allocateUtf8String(signature),
                    nativeBody,
                    (long) body.length);
            return result == 1;
        } catch (Throwable exc) {
            throw new LsmsgException("Failed to verify Slack signature", exc);
        }
    }

    String parseSlackWebhook(byte[] body, String contentType) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment nativeBody = nativeBytes(arena, body);
            MemorySegment result = (MemorySegment) slackParseWebhook.invoke(
                    nativeBody,
                    (long) body.length,
                    arena.allocateUtf8String(contentType));
            return takeOwnedString(result);
        } catch (Throwable exc) {
            throw new LsmsgException("Failed to parse Slack webhook", exc);
        }
    }

    String stripSlackMentions(String text) {
        return invokeOwnedString(slackStripMentions, text, "Failed to strip Slack mentions");
    }

    String parseTeamsWebhook(String payloadJson) {
        return invokeOwnedString(teamsParseWebhook, payloadJson, "Failed to parse Teams webhook");
    }

    String stripTeamsMentions(String text) {
        return invokeOwnedString(teamsStripMentions, text, "Failed to strip Teams mentions");
    }

    String deterministicThreadId(String platform, String workspaceId, String channelId, String threadId) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment result = (MemorySegment) deterministicThreadId.invoke(
                    arena.allocateUtf8String(platform),
                    arena.allocateUtf8String(workspaceId),
                    arena.allocateUtf8String(channelId),
                    arena.allocateUtf8String(threadId));
            return takeOwnedString(result);
        } catch (Throwable exc) {
            throw new LsmsgException("Failed to compute deterministic thread ID", exc);
        }
    }

    private String invokeOwnedString(MethodHandle handle, String value, String message) {
        try (Arena arena = Arena.ofConfined()) {
            MemorySegment result = (MemorySegment) handle.invoke(arena.allocateUtf8String(value));
            return takeOwnedString(result);
        } catch (Throwable exc) {
            throw new LsmsgException(message, exc);
        }
    }

    private String takeOwnedString(MemorySegment result) throws Throwable {
        if (result.address() == 0) {
            return null;
        }
        String value = result.reinterpret(Long.MAX_VALUE).getUtf8String(0);
        freeString.invoke(result);
        return value;
    }

    private static MethodHandle downcall(
            Linker linker, SymbolLookup lookup, String symbolName, FunctionDescriptor descriptor) {
        MemorySegment symbol = lookup.find(symbolName).orElseThrow(() -> new LsmsgException("Missing native symbol: " + symbolName));
        return linker.downcallHandle(symbol, descriptor);
    }

    private static MemorySegment nativeBytes(Arena arena, byte[] value) {
        MemorySegment segment = arena.allocate(Math.max(1, value.length));
        if (value.length > 0) {
            segment.asByteBuffer().put(value);
        }
        return segment;
    }

    private static Path resolveLibraryPath() {
        String explicit = System.getProperty("lsmsg.ffi.lib");
        if (explicit != null && !explicit.isBlank()) {
            return Path.of(explicit).toAbsolutePath();
        }
        String env = System.getenv("LSMSG_FFI_LIB");
        if (env != null && !env.isBlank()) {
            return Path.of(env).toAbsolutePath();
        }

        Path current = Path.of("").toAbsolutePath();
        String fileName = libraryFileName();
        for (Path dir = current; dir != null; dir = dir.getParent()) {
            Path candidate = dir.resolve("target").resolve("release").resolve(fileName);
            if (Files.exists(candidate)) {
                return candidate;
            }
        }
        throw new LsmsgException("Unable to locate " + fileName + ". Set LSMSG_FFI_LIB or -Dlsmsg.ffi.lib.");
    }

    private static String libraryFileName() {
        String os = System.getProperty("os.name", "").toLowerCase();
        if (os.contains("mac")) {
            return "liblsmsg_ffi.dylib";
        }
        if (os.contains("win")) {
            return "lsmsg_ffi.dll";
        }
        return "liblsmsg_ffi.so";
    }
}
