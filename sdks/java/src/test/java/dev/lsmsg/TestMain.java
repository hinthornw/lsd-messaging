package dev.lsmsg;

public final class TestMain {
    private TestMain() {}

    public static void main(String[] args) {
        NativeBridgeTest nativeBridgeTest = new NativeBridgeTest();
        LangGraphClientTest langGraphClientTest = new LangGraphClientTest();
        BotTest botTest = new BotTest();

        TestSupport.run("NativeBridgeTest.parsesSlackUrlVerificationChallenge", nativeBridgeTest::parsesSlackUrlVerificationChallenge);
        TestSupport.run("NativeBridgeTest.parsesTeamsMessageEvent", nativeBridgeTest::parsesTeamsMessageEvent);
        TestSupport.run("NativeBridgeTest.computesDeterministicThreadIds", nativeBridgeTest::computesDeterministicThreadIds);
        TestSupport.run("LangGraphClientTest.createsWaitsAndStreamsRuns", langGraphClientTest::createsWaitsAndStreamsRuns);
        TestSupport.run("BotTest.rejectsMissingSlackSignatureHeaders", botTest::rejectsMissingSlackSignatureHeaders);
        TestSupport.run("BotTest.dispatchesSlackMentionHandlers", botTest::dispatchesSlackMentionHandlers);
        TestSupport.run("BotTest.invokesLangGraphFromHandlers", botTest::invokesLangGraphFromHandlers);
    }
}
