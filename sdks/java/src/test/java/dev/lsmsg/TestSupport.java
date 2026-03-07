package dev.lsmsg;

final class TestSupport {
    private TestSupport() {}

    static void run(String name, ThrowingRunnable runnable) {
        try {
            runnable.run();
            System.out.println("PASS " + name);
        } catch (Throwable exc) {
            System.err.println("FAIL " + name + ": " + exc.getMessage());
            throw exc instanceof RuntimeException runtime ? runtime : new RuntimeException(exc);
        }
    }

    static void assertTrue(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + " expected=" + expected + " actual=" + actual);
        }
    }

    static void assertNotNull(Object value, String message) {
        if (value == null) {
            throw new AssertionError(message);
        }
    }

    @FunctionalInterface
    interface ThrowingRunnable {
        void run() throws Exception;
    }
}

