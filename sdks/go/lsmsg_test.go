package lsmsg

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// mockBackend implements ffiBackend for unit testing without the Rust shared library.
type mockBackend struct {
	verifyResult       bool
	langGraphHandle    int64
	langGraphHandleSet bool
	parseSlackFn       func(body []byte, contentType string) (*Event, string, error)
	parseTeamsFn       func(payloadJSON []byte) (*Event, error)
	stripSlackFn       func(text string) string
	stripTeamsFn       func(text string) string
	threadIDFn         func(platform, workspaceID, channelID, threadID string) string
	createRunFn        func(handle int64, paramsJSON []byte) (string, error)
	waitRunFn          func(handle int64, threadID, runID string) (*RunResult, error)
	cancelRunFn        func(handle int64, threadID, runID string) error
}

func (m *mockBackend) SlackVerifySignature(_, _, _ string, _ []byte) bool {
	return m.verifyResult
}

func (m *mockBackend) SlackParseWebhook(body []byte, contentType string) (*Event, string, error) {
	if m.parseSlackFn != nil {
		return m.parseSlackFn(body, contentType)
	}
	return nil, "", nil
}

func (m *mockBackend) TeamsParseWebhook(payloadJSON []byte) (*Event, error) {
	if m.parseTeamsFn != nil {
		return m.parseTeamsFn(payloadJSON)
	}
	return nil, nil
}

func (m *mockBackend) SlackStripMentions(text string) string {
	if m.stripSlackFn != nil {
		return m.stripSlackFn(text)
	}
	return text
}

func (m *mockBackend) TeamsStripMentions(text string) string {
	if m.stripTeamsFn != nil {
		return m.stripTeamsFn(text)
	}
	return text
}

func (m *mockBackend) DeterministicThreadID(platform, workspaceID, channelID, threadID string) string {
	if m.threadIDFn != nil {
		return m.threadIDFn(platform, workspaceID, channelID, threadID)
	}
	return platform + ":" + workspaceID + ":" + channelID + ":" + threadID
}

func (m *mockBackend) RegistryNew() int64                       { return 1 }
func (m *mockBackend) RegistryFree(_ int64)                     {}
func (m *mockBackend) RegistryRegister(_ int64, _ []byte) int64 { return 1 }
func (m *mockBackend) RegistryMatchEvent(_ int64, _ []byte) ([]int64, error) {
	return nil, nil
}

func (m *mockBackend) LangGraphNew(_, _ string) int64 {
	if m.langGraphHandleSet {
		return m.langGraphHandle
	}
	return 1
}

func (m *mockBackend) LangGraphFree(_ int64) {}

func (m *mockBackend) LangGraphCreateRun(handle int64, paramsJSON []byte) (string, error) {
	if m.createRunFn != nil {
		return m.createRunFn(handle, paramsJSON)
	}
	return "run-123", nil
}

func (m *mockBackend) LangGraphWaitRun(handle int64, threadID, runID string) (*RunResult, error) {
	if m.waitRunFn != nil {
		return m.waitRunFn(handle, threadID, runID)
	}
	return &RunResult{
		ID:     runID,
		Status: "completed",
		Output: json.RawMessage(`{"messages":[{"content":"hello"}]}`),
	}, nil
}

func (m *mockBackend) LangGraphCancelRun(handle int64, threadID, runID string) error {
	if m.cancelRunFn != nil {
		return m.cancelRunFn(handle, threadID, runID)
	}
	return nil
}

// --- Tests ---

func TestNewBot(t *testing.T) {
	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, &mockBackend{})

	if bot == nil {
		t.Fatal("expected bot to be non-nil")
	}
	if bot.config.MaxBodyBytes == 0 {
		t.Error("expected default MaxBodyBytes")
	}
	if bot.config.MaxPendingTasks == 0 {
		t.Error("expected default MaxPendingTasks")
	}
}

func TestHandlerRegistration(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	var called []string

	bot.OnMention(func(e *Event) error {
		called = append(called, "mention")
		return nil
	})
	bot.OnMessage(func(e *Event) error {
		called = append(called, "message")
		return nil
	})
	bot.Command("/ask", func(e *Event) error {
		called = append(called, "command:/ask")
		return nil
	})
	bot.OnReaction("thumbsup", func(e *Event) error {
		called = append(called, "reaction:thumbsup")
		return nil
	})
	bot.On("app_home_opened", func(e *Event) error {
		called = append(called, "raw:app_home_opened")
		return nil
	})

	if len(bot.handlers) != 5 {
		t.Fatalf("expected 5 handlers, got %d", len(bot.handlers))
	}
}

func TestHandlerMatching(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	var result string

	bot.OnMention(func(e *Event) error {
		result = "mention"
		return nil
	})
	bot.OnMessage(func(e *Event) error {
		result = "message"
		return nil
	})
	bot.Command("/ask", func(e *Event) error {
		result = "command"
		return nil
	})
	bot.OnReaction("thumbsup", func(e *Event) error {
		result = "reaction"
		return nil
	})

	tests := []struct {
		name     string
		event    Event
		expected string
	}{
		{
			name:     "mention event",
			event:    Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected: "mention",
		},
		{
			name:     "message event",
			event:    Event{Kind: EventMessage, Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected: "message",
		},
		{
			name:     "command event",
			event:    Event{Kind: EventCommand, Command: "/ask", Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected: "command",
		},
		{
			name:     "reaction event",
			event:    Event{Kind: EventReaction, Emoji: "thumbsup", Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected: "reaction",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			result = ""
			bot.dispatch(&tc.event)
			if result != tc.expected {
				t.Errorf("expected %q, got %q", tc.expected, result)
			}
		})
	}
}

func TestHandlerMatchingWithPlatformFilter(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	var result string

	bot.OnMentionWithOpts(func(e *Event) error {
		result = "slack-mention"
		return nil
	}, HandlerOpts{Platform: PlatformSlack})

	bot.OnMentionWithOpts(func(e *Event) error {
		result = "teams-mention"
		return nil
	}, HandlerOpts{Platform: PlatformTeams})

	// Slack mention should only match the first handler.
	result = ""
	bot.dispatch(&Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformSlack}})
	if result != "slack-mention" {
		t.Errorf("expected slack-mention, got %q", result)
	}

	// Teams mention should only match the second handler.
	result = ""
	bot.dispatch(&Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformTeams}})
	if result != "teams-mention" {
		t.Errorf("expected teams-mention, got %q", result)
	}
}

func TestHandlerMatchingWithPattern(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	var result string

	bot.OnMessageWithOpts(func(e *Event) error {
		result = "matched"
		return nil
	}, HandlerOpts{Pattern: `hello\s+world`})

	result = ""
	bot.dispatch(&Event{Kind: EventMessage, Text: "hello world", Platform: PlatformCapabilities{Name: PlatformSlack}})
	if result != "matched" {
		t.Errorf("expected matched, got %q", result)
	}

	result = ""
	bot.dispatch(&Event{Kind: EventMessage, Text: "goodbye", Platform: PlatformCapabilities{Name: PlatformSlack}})
	if result != "" {
		t.Errorf("expected empty, got %q", result)
	}
}

func TestSlackWebhookHandler(t *testing.T) {
	var dispatched bool
	mock := &mockBackend{
		verifyResult: true,
		parseSlackFn: func(body []byte, contentType string) (*Event, string, error) {
			return &Event{
				Kind:     EventMention,
				Platform: PlatformCapabilities{Name: PlatformSlack},
				Text:     "hello",
			}, "", nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	bot.OnMention(func(e *Event) error {
		dispatched = true
		return nil
	})

	req := httptest.NewRequest(http.MethodPost, "/slack/events", strings.NewReader(`{"event":{"type":"app_mention"}}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Slack-Request-Timestamp", "1234567890")
	req.Header.Set("X-Slack-Signature", "v0=abc")

	w := httptest.NewRecorder()
	bot.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !dispatched {
		t.Error("expected handler to be dispatched")
	}
}

func TestSlackWebhookSignatureFailure(t *testing.T) {
	mock := &mockBackend{
		verifyResult: false,
	}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	req := httptest.NewRequest(http.MethodPost, "/slack/events", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Slack-Request-Timestamp", "1234567890")
	req.Header.Set("X-Slack-Signature", "v0=bad")

	w := httptest.NewRecorder()
	bot.HandleSlackWebhook(w, req)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected 401, got %d", w.Code)
	}
}

func TestSlackURLVerificationChallenge(t *testing.T) {
	mock := &mockBackend{
		verifyResult: true,
		parseSlackFn: func(body []byte, contentType string) (*Event, string, error) {
			return nil, "challenge-token-123", nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	req := httptest.NewRequest(http.MethodPost, "/slack/events", strings.NewReader(`{"type":"url_verification","challenge":"challenge-token-123"}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Slack-Request-Timestamp", "1234567890")
	req.Header.Set("X-Slack-Signature", "v0=abc")

	w := httptest.NewRecorder()
	bot.HandleSlackWebhook(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var resp map[string]string
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["challenge"] != "challenge-token-123" {
		t.Errorf("expected challenge token, got %v", resp)
	}
}

func TestTeamsWebhookHandler(t *testing.T) {
	var dispatched bool
	mock := &mockBackend{
		parseTeamsFn: func(payloadJSON []byte) (*Event, error) {
			return &Event{
				Kind:     EventMessage,
				Platform: PlatformCapabilities{Name: PlatformTeams},
				Text:     "hello teams",
			}, nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		Teams: &TeamsConfig{AppID: "app-id"},
	}, mock)

	bot.OnMessage(func(e *Event) error {
		dispatched = true
		if e.Text != "hello teams" {
			t.Errorf("expected 'hello teams', got %q", e.Text)
		}
		return nil
	})

	req := httptest.NewRequest(http.MethodPost, "/teams/events", strings.NewReader(`{"type":"message"}`))
	req.Header.Set("Content-Type", "application/json")

	w := httptest.NewRecorder()
	bot.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !dispatched {
		t.Error("expected handler to be dispatched")
	}
}

func TestMethodNotAllowed(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	req := httptest.NewRequest(http.MethodGet, "/slack/events", nil)
	w := httptest.NewRecorder()
	bot.ServeHTTP(w, req)

	if w.Code != http.StatusMethodNotAllowed {
		t.Errorf("expected 405, got %d", w.Code)
	}
}

func TestUnknownPath(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	req := httptest.NewRequest(http.MethodPost, "/unknown", strings.NewReader(`{}`))
	w := httptest.NewRecorder()
	bot.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404, got %d", w.Code)
	}
}

func TestEventInvoke(t *testing.T) {
	mock := &mockBackend{
		createRunFn: func(handle int64, paramsJSON []byte) (string, error) {
			var params map[string]any
			json.Unmarshal(paramsJSON, &params)
			if params["agent"] != "my-agent" {
				t.Errorf("expected agent=my-agent, got %v", params["agent"])
			}
			return "run-456", nil
		},
		waitRunFn: func(handle int64, threadID, runID string) (*RunResult, error) {
			if runID != "run-456" {
				t.Errorf("expected runID=run-456, got %s", runID)
			}
			return &RunResult{
				ID:     runID,
				Status: "completed",
				Output: json.RawMessage(`{"messages":[{"content":"bot says hi"}]}`),
			}, nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		LangGraph: &LangGraphConfig{URL: "http://localhost:8123"},
	}, mock)

	event := &Event{
		Kind:        EventMention,
		Platform:    PlatformCapabilities{Name: PlatformSlack},
		WorkspaceID: "T1",
		ChannelID:   "C1",
		ThreadID:    "t1",
		Text:        "hello bot",
		bot:         bot,
	}

	result, err := event.Invoke("my-agent")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Text() != "bot says hi" {
		t.Errorf("expected 'bot says hi', got %q", result.Text())
	}
}

func TestEventInvokeAllowsZeroLangGraphHandle(t *testing.T) {
	mock := &mockBackend{
		langGraphHandle:    0,
		langGraphHandleSet: true,
		createRunFn: func(handle int64, paramsJSON []byte) (string, error) {
			if handle != 0 {
				t.Errorf("expected zero handle, got %d", handle)
			}
			return "run-0", nil
		},
		waitRunFn: func(handle int64, threadID, runID string) (*RunResult, error) {
			if handle != 0 {
				t.Errorf("expected zero handle, got %d", handle)
			}
			return &RunResult{
				ID:     runID,
				Status: "completed",
				Output: json.RawMessage(`{"messages":[{"content":"zero handle works"}]}`),
			}, nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		LangGraph: &LangGraphConfig{URL: "http://localhost:8123"},
	}, mock)

	event := &Event{
		Kind:        EventMention,
		Platform:    PlatformCapabilities{Name: PlatformSlack},
		WorkspaceID: "T1",
		ChannelID:   "C1",
		ThreadID:    "t1",
		Text:        "hello bot",
		bot:         bot,
	}

	result, err := event.Invoke("my-agent")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Text() != "zero handle works" {
		t.Errorf("expected zero-handle result text, got %q", result.Text())
	}
}

func TestEventInvokeNoBotError(t *testing.T) {
	event := &Event{Kind: EventMention}
	_, err := event.Invoke("agent")
	if err == nil {
		t.Fatal("expected error for event without bot")
	}
}

func TestEventInvokeNoLangGraphError(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})
	event := &Event{Kind: EventMention, bot: bot}
	_, err := event.Invoke("agent")
	if err == nil {
		t.Fatal("expected error when LangGraph not configured")
	}
}

func TestEventReplyNoBotError(t *testing.T) {
	event := &Event{Kind: EventMention}
	err := event.Reply("hello")
	if err == nil {
		t.Fatal("expected error for event without bot")
	}
}

func TestEventReplySlackNoToken(t *testing.T) {
	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{},
	}, &mockBackend{})
	event := &Event{
		Kind:     EventMention,
		Platform: PlatformCapabilities{Name: PlatformSlack},
		bot:      bot,
	}
	err := event.Reply("hello")
	if err == nil {
		t.Fatal("expected error when bot token not configured")
	}
}

func TestEventReplySlack(t *testing.T) {
	// Set up a mock Slack API server.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/chat.postMessage" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		auth := r.Header.Get("Authorization")
		if auth != "Bearer xoxb-test-token" {
			t.Errorf("unexpected auth: %s", auth)
		}
		body, _ := io.ReadAll(r.Body)
		var payload map[string]string
		json.Unmarshal(body, &payload)
		if payload["channel"] != "C123" {
			t.Errorf("expected channel C123, got %s", payload["channel"])
		}
		if payload["text"] != "reply text" {
			t.Errorf("expected text 'reply text', got %s", payload["text"])
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{BotToken: "xoxb-test-token"},
	}, &mockBackend{})
	bot.httpClient = server.Client()
	bot.slackAPIBaseURL = server.URL
	event := &Event{
		Kind:      EventMention,
		Platform:  PlatformCapabilities{Name: PlatformSlack},
		ChannelID: "C123",
		ThreadID:  "t123",
		bot:       bot,
	}

	if err := event.Reply("reply text"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestEventReplySlackAPIErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"ok":false,"error":"channel_not_found"}`))
	}))
	defer server.Close()

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{BotToken: "xoxb-test-token"},
	}, &mockBackend{})
	bot.httpClient = server.Client()
	bot.slackAPIBaseURL = server.URL

	event := &Event{
		Kind:      EventMention,
		Platform:  PlatformCapabilities{Name: PlatformSlack},
		ChannelID: "C123",
		ThreadID:  "t123",
		bot:       bot,
	}

	err := event.Reply("reply text")
	if err == nil || !strings.Contains(err.Error(), "channel_not_found") {
		t.Fatalf("expected Slack API error, got %v", err)
	}
}

func TestRunResultText(t *testing.T) {
	tests := []struct {
		name     string
		output   string
		expected string
	}{
		{
			name:     "with messages",
			output:   `{"messages":[{"content":"first"},{"content":"last"}]}`,
			expected: "last",
		},
		{
			name:     "empty messages",
			output:   `{"messages":[]}`,
			expected: "",
		},
		{
			name:     "no messages key",
			output:   `{"other":"value"}`,
			expected: "",
		},
		{
			name:     "invalid json",
			output:   `not json`,
			expected: "",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			rr := &RunResult{Output: json.RawMessage(tc.output)}
			if got := rr.Text(); got != tc.expected {
				t.Errorf("expected %q, got %q", tc.expected, got)
			}
		})
	}
}

func TestMultipleHandlersDispatched(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	var calls []string

	bot.OnMention(func(e *Event) error {
		calls = append(calls, "handler1")
		return nil
	})
	bot.OnMention(func(e *Event) error {
		calls = append(calls, "handler2")
		return nil
	})

	event := &Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformSlack}}
	bot.dispatch(event)

	if len(calls) != 2 {
		t.Fatalf("expected 2 calls, got %d", len(calls))
	}
	if calls[0] != "handler1" || calls[1] != "handler2" {
		t.Errorf("unexpected call order: %v", calls)
	}
}

func TestNoMatchingHandler(t *testing.T) {
	bot := newBotWithBackend(BotConfig{}, &mockBackend{})

	bot.OnMention(func(e *Event) error {
		t.Error("should not be called")
		return nil
	})

	event := &Event{Kind: EventMessage, Platform: PlatformCapabilities{Name: PlatformSlack}}
	err := bot.dispatch(event)
	if err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestAutoDetectSlackByHeader(t *testing.T) {
	var dispatched bool
	mock := &mockBackend{
		verifyResult: true,
		parseSlackFn: func(body []byte, contentType string) (*Event, string, error) {
			return &Event{
				Kind:     EventMention,
				Platform: PlatformCapabilities{Name: PlatformSlack},
			}, "", nil
		},
	}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	bot.OnMention(func(e *Event) error {
		dispatched = true
		return nil
	})

	// Send to a non-standard path but with Slack headers.
	req := httptest.NewRequest(http.MethodPost, "/webhook", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Slack-Signature", "v0=abc")
	req.Header.Set("X-Slack-Request-Timestamp", "12345")

	w := httptest.NewRecorder()
	bot.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	if !dispatched {
		t.Error("expected Slack handler to be auto-detected and dispatched")
	}
}
