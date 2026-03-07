package dev.lsmsg;

public final class LsmsgException extends RuntimeException {
    public LsmsgException(String message) {
        super(message);
    }

    public LsmsgException(String message, Throwable cause) {
        super(message, cause);
    }
}

