package dev.lsmsg;

@FunctionalInterface
public interface EventHandler {
    void handle(Event event) throws Exception;
}

