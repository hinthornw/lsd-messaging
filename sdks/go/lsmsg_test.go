package lsmsg

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
)

// mockBackend implements ffiBackend for unit testing without the Rust shared library.
type registeredFilter struct {
	ID     int64
	Fields map[string]any
}

type mockBackend struct {
	verifyResult       bool
	langGraphHandle    int64
	langGraphHandleSet bool
	registryHandle     int64
	registryHandleSet  bool
	registeredFilters  []registeredFilter
	nextHandlerID      int64
	parseSlackFn       func(body []byte, contentType string) (*Event, string, error)
	parseTeamsFn       func(payloadJSON []byte) (*Event, error)
	stripSlackFn       func(text string) string
	stripTeamsFn       func(text string) string
	threadIDFn         func(platform, workspaceID, channelID, threadID string) string
	registryNewFn      func() int64
	registryRegisterFn func(handle int64, fieldsJSON []byte) int64
	registryMatchFn    func(handle int64, eventJSON []byte) ([]int64, error)
	processSlackFn     func(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error)
	processTeamsFn     func(handle int64, body []byte) (*webhookOutcome, error)
	createRunFn        func(handle int64, paramsJSON []byte) (string, error)
	waitRunFn          func(handle int64, threadID, runID string) (*RunResult, error)
	cancelRunFn        func(handle int64, threadID, runID string) error
	freedRegistry      []int64
	freedLangGraph     []int64
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

func (m *mockBackend) RegistryNew() int64 {
	if m.registryNewFn != nil {
		return m.registryNewFn()
	}
	if m.registryHandleSet {
		return m.registryHandle
	}
	return 0
}

func (m *mockBackend) RegistryFree(handle int64) {
	m.freedRegistry = append(m.freedRegistry, handle)
}

func (m *mockBackend) RegistryRegister(handle int64, fieldsJSON []byte) int64 {
	if m.registryRegisterFn != nil {
		return m.registryRegisterFn(handle, fieldsJSON)
	}

	var fields map[string]any
	if err := json.Unmarshal(fieldsJSON, &fields); err != nil {
		return -1
	}
	m.nextHandlerID++
	id := m.nextHandlerID
	m.registeredFilters = append(m.registeredFilters, registeredFilter{
		ID:     id,
		Fields: fields,
	})
	return id
}

func (m *mockBackend) RegistryMatchEvent(handle int64, eventJSON []byte) ([]int64, error) {
	if m.registryMatchFn != nil {
		return m.registryMatchFn(handle, eventJSON)
	}

	var event Event
	if err := json.Unmarshal(eventJSON, &event); err != nil {
		return nil, err
	}

	var matched []int64
	for _, filter := range m.registeredFilters {
		if matchesRegisteredFilter(filter.Fields, &event) {
			matched = append(matched, filter.ID)
		}
	}
	return matched, nil
}

func (m *mockBackend) RegistryProcessSlackWebhook(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
	if m.processSlackFn != nil {
		return m.processSlackFn(handle, body, contentType, signingSecret, timestamp, signature)
	}

	if signingSecret != "" {
		if timestamp == "" || signature == "" {
			return &webhookOutcome{
				Type:       "rejected",
				StatusCode: http.StatusUnauthorized,
				Error:      "missing signature headers",
			}, nil
		}
		if !m.verifyResult {
			return &webhookOutcome{
				Type:       "rejected",
				StatusCode: http.StatusUnauthorized,
				Error:      "invalid signature",
			}, nil
		}
	}

	if m.parseSlackFn == nil {
		return &webhookOutcome{Type: "ignored"}, nil
	}

	event, challenge, err := m.parseSlackFn(body, contentType)
	if err != nil {
		return &webhookOutcome{
			Type:       "rejected",
			StatusCode: http.StatusBadRequest,
			Error:      err.Error(),
		}, nil
	}
	if challenge != "" {
		return &webhookOutcome{Type: "challenge", Challenge: challenge}, nil
	}
	if event == nil {
		return &webhookOutcome{Type: "ignored"}, nil
	}

	eventJSON, err := json.Marshal(event)
	if err != nil {
		return nil, err
	}
	handlerIDs, err := m.RegistryMatchEvent(handle, eventJSON)
	if err != nil {
		return nil, err
	}
	if len(handlerIDs) == 0 {
		return &webhookOutcome{Type: "ignored"}, nil
	}
	return &webhookOutcome{
		Type:       "dispatch",
		Event:      event,
		HandlerIDs: handlerIDs,
	}, nil
}

func (m *mockBackend) RegistryProcessTeamsWebhook(handle int64, body []byte) (*webhookOutcome, error) {
	if m.processTeamsFn != nil {
		return m.processTeamsFn(handle, body)
	}

	if m.parseTeamsFn == nil {
		return &webhookOutcome{Type: "ignored"}, nil
	}

	event, err := m.parseTeamsFn(body)
	if err != nil {
		return &webhookOutcome{
			Type:       "rejected",
			StatusCode: http.StatusBadRequest,
			Error:      err.Error(),
		}, nil
	}
	if event == nil {
		return &webhookOutcome{Type: "ignored"}, nil
	}

	eventJSON, err := json.Marshal(event)
	if err != nil {
		return nil, err
	}
	handlerIDs, err := m.RegistryMatchEvent(handle, eventJSON)
	if err != nil {
		return nil, err
	}
	if len(handlerIDs) == 0 {
		return &webhookOutcome{Type: "ignored"}, nil
	}
	return &webhookOutcome{
		Type:       "dispatch",
		Event:      event,
		HandlerIDs: handlerIDs,
	}, nil
}

func (m *mockBackend) LangGraphNew(_, _ string) int64 {
	if m.langGraphHandleSet {
		return m.langGraphHandle
	}
	return 1
}

func (m *mockBackend) LangGraphFree(handle int64) {
	m.freedLangGraph = append(m.freedLangGraph, handle)
}

func matchesRegisteredFilter(fields map[string]any, event *Event) bool {
	if eventKind, ok := fields["event_kind"].(string); ok && eventKind != "" && string(event.Kind) != eventKind {
		return false
	}
	if platform, ok := fields["platform"].(string); ok && platform != "" && string(event.Platform.Name) != platform {
		return false
	}
	if command, ok := fields["command"].(string); ok && command != "" && event.Command != command {
		return false
	}
	if emoji, ok := fields["emoji"].(string); ok && emoji != "" && event.Emoji != emoji {
		return false
	}
	if rawEventType, ok := fields["raw_event_type"].(string); ok && rawEventType != "" && event.RawEventType != rawEventType {
		return false
	}
	if pattern, ok := fields["pattern"].(string); ok && pattern != "" {
		re, err := regexp.Compile(pattern)
		if err != nil || !re.MatchString(event.Text) {
			return false
		}
	}
	return true
}

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
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	if bot == nil {
		t.Fatal("expected bot to be non-nil")
	}
	if bot.config.MaxBodyBytes == 0 {
		t.Error("expected default MaxBodyBytes")
	}
	if bot.config.MaxPendingTasks == 0 {
		t.Error("expected default MaxPendingTasks")
	}
	if bot.registryHandle != 0 {
		t.Errorf("expected zero registry handle to remain valid, got %d", bot.registryHandle)
	}
}

func TestBotCloseReleasesNativeHandlesOnce(t *testing.T) {
	mock := &mockBackend{
		registryHandleSet:  true,
		registryHandle:     0,
		langGraphHandleSet: true,
		langGraphHandle:    0,
	}
	bot := newBotWithBackend(BotConfig{
		LangGraph: &LangGraphConfig{URL: "http://localhost:8123"},
	}, mock)

	bot.Close()
	bot.Close()

	if len(mock.freedRegistry) != 1 || mock.freedRegistry[0] != 0 {
		t.Fatalf("expected registry handle 0 to be freed once, got %#v", mock.freedRegistry)
	}
	if len(mock.freedLangGraph) != 1 || mock.freedLangGraph[0] != 0 {
		t.Fatalf("expected LangGraph handle 0 to be freed once, got %#v", mock.freedLangGraph)
	}
}

func TestHandlerRegistration(t *testing.T) {
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

	bot.OnMention(func(e *Event) error { return nil })
	bot.OnMessage(func(e *Event) error { return nil })
	bot.Command("/ask", func(e *Event) error { return nil })
	bot.OnReaction("thumbsup", func(e *Event) error { return nil })
	bot.On("app_home_opened", func(e *Event) error { return nil })

	if len(bot.handlers) != 5 {
		t.Fatalf("expected 5 handlers, got %d", len(bot.handlers))
	}
	if len(mock.registeredFilters) != 5 {
		t.Fatalf("expected 5 registered filters, got %d", len(mock.registeredFilters))
	}
	if got := mock.registeredFilters[0].Fields["event_kind"]; got != string(EventMention) {
		t.Fatalf("expected first filter to register mention kind, got %v", got)
	}
	if got := mock.registeredFilters[2].Fields["command"]; got != "/ask" {
		t.Fatalf("expected command filter to register /ask, got %v", got)
	}
	if got := mock.registeredFilters[3].Fields["emoji"]; got != "thumbsup" {
		t.Fatalf("expected reaction filter to register thumbsup, got %v", got)
	}
	if got := mock.registeredFilters[4].Fields["raw_event_type"]; got != "app_home_opened" {
		t.Fatalf("expected raw event type to be registered, got %v", got)
	}
}

func TestDispatchUsesRegistryMatchResult(t *testing.T) {
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

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
		name      string
		handlerID int64
		event     Event
		expected  string
	}{
		{
			name:      "mention event",
			handlerID: mock.registeredFilters[0].ID,
			event:     Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected:  "mention",
		},
		{
			name:      "message event",
			handlerID: mock.registeredFilters[1].ID,
			event:     Event{Kind: EventMessage, Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected:  "message",
		},
		{
			name:      "command event",
			handlerID: mock.registeredFilters[2].ID,
			event:     Event{Kind: EventCommand, Command: "/ask", Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected:  "command",
		},
		{
			name:      "reaction event",
			handlerID: mock.registeredFilters[3].ID,
			event:     Event{Kind: EventReaction, Emoji: "thumbsup", Platform: PlatformCapabilities{Name: PlatformSlack}},
			expected:  "reaction",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			mock.registryMatchFn = func(handle int64, eventJSON []byte) ([]int64, error) {
				if handle != bot.registryHandle {
					t.Fatalf("expected registry handle %d, got %d", bot.registryHandle, handle)
				}
				var event Event
				if err := json.Unmarshal(eventJSON, &event); err != nil {
					t.Fatalf("expected dispatch to marshal an event, got %v", err)
				}
				if event.Kind != tc.event.Kind {
					t.Fatalf("expected event kind %s, got %s", tc.event.Kind, event.Kind)
				}
				return []int64{tc.handlerID}, nil
			}
			result = ""
			if err := bot.dispatch(&tc.event); err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if result != tc.expected {
				t.Errorf("expected %q, got %q", tc.expected, result)
			}
		})
	}
}

func TestHandlerRegistrationIncludesPlatformFilter(t *testing.T) {
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

	bot.OnMentionWithOpts(func(e *Event) error { return nil }, HandlerOpts{Platform: PlatformSlack})
	bot.OnMentionWithOpts(func(e *Event) error { return nil }, HandlerOpts{Platform: PlatformTeams})

	if got := mock.registeredFilters[0].Fields["platform"]; got != string(PlatformSlack) {
		t.Fatalf("expected slack platform filter, got %v", got)
	}
	if got := mock.registeredFilters[1].Fields["platform"]; got != string(PlatformTeams) {
		t.Fatalf("expected teams platform filter, got %v", got)
	}
}

func TestHandlerRegistrationIncludesPatternFilter(t *testing.T) {
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

	bot.OnMessageWithOpts(func(e *Event) error { return nil }, HandlerOpts{Pattern: `hello\s+world`})

	if got := mock.registeredFilters[0].Fields["pattern"]; got != `hello\s+world` {
		t.Fatalf("expected pattern filter to be registered, got %v", got)
	}
}

func TestHandlerRegistrationPanicsWhenRegistryRejectsFilter(t *testing.T) {
	mock := &mockBackend{
		registryRegisterFn: func(handle int64, fieldsJSON []byte) int64 { return -1 },
	}
	bot := newBotWithBackend(BotConfig{}, mock)

	defer func() {
		if r := recover(); r == nil {
			t.Fatal("expected invalid handler registration to panic")
		}
	}()

	bot.OnMessageWithOpts(func(e *Event) error { return nil }, HandlerOpts{Pattern: "("})
}

func TestSlackWebhookHandler(t *testing.T) {
	var dispatched bool
	mock := &mockBackend{}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	bot.OnMention(func(e *Event) error {
		dispatched = true
		return nil
	})

	mentionID := mock.registeredFilters[0].ID
	mock.processSlackFn = func(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
		if handle != bot.registryHandle {
			t.Fatalf("expected registry handle %d, got %d", bot.registryHandle, handle)
		}
		if signingSecret != "secret" {
			t.Fatalf("expected signing secret to be forwarded, got %q", signingSecret)
		}
		if timestamp != "1234567890" {
			t.Fatalf("expected timestamp to be forwarded, got %q", timestamp)
		}
		if signature != "v0=abc" {
			t.Fatalf("expected signature to be forwarded, got %q", signature)
		}
		return &webhookOutcome{
			Type: "dispatch",
			Event: &Event{
				Kind:     EventMention,
				Platform: PlatformCapabilities{Name: PlatformSlack},
				Text:     "hello",
			},
			HandlerIDs: []int64{mentionID},
		}, nil
	}

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
		processSlackFn: func(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
			return &webhookOutcome{
				Type:       "rejected",
				StatusCode: http.StatusUnauthorized,
				Error:      "invalid signature",
			}, nil
		},
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
		processSlackFn: func(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
			return &webhookOutcome{
				Type:      "challenge",
				Challenge: "challenge-token-123",
			}, nil
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
	mock := &mockBackend{}

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

	messageID := mock.registeredFilters[0].ID
	mock.processTeamsFn = func(handle int64, body []byte) (*webhookOutcome, error) {
		if handle != bot.registryHandle {
			t.Fatalf("expected registry handle %d, got %d", bot.registryHandle, handle)
		}
		return &webhookOutcome{
			Type: "dispatch",
			Event: &Event{
				Kind:     EventMessage,
				Platform: PlatformCapabilities{Name: PlatformTeams},
				Text:     "hello teams",
			},
			HandlerIDs: []int64{messageID},
		}, nil
	}

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
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

	var calls []string

	bot.OnMention(func(e *Event) error {
		calls = append(calls, "handler1")
		return nil
	})
	bot.OnMention(func(e *Event) error {
		calls = append(calls, "handler2")
		return nil
	})

	firstID := mock.registeredFilters[0].ID
	secondID := mock.registeredFilters[1].ID
	mock.registryMatchFn = func(handle int64, eventJSON []byte) ([]int64, error) {
		return []int64{firstID, secondID}, nil
	}

	event := &Event{Kind: EventMention, Platform: PlatformCapabilities{Name: PlatformSlack}}
	if err := bot.dispatch(event); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if len(calls) != 2 {
		t.Fatalf("expected 2 calls, got %d", len(calls))
	}
	if calls[0] != "handler1" || calls[1] != "handler2" {
		t.Errorf("unexpected call order: %v", calls)
	}
}

func TestNoMatchingHandler(t *testing.T) {
	mock := &mockBackend{}
	bot := newBotWithBackend(BotConfig{}, mock)

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
	mock := &mockBackend{}

	bot := newBotWithBackend(BotConfig{
		Slack: &SlackConfig{SigningSecret: "secret"},
	}, mock)

	bot.OnMention(func(e *Event) error {
		dispatched = true
		return nil
	})

	mentionID := mock.registeredFilters[0].ID
	mock.processSlackFn = func(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
		return &webhookOutcome{
			Type: "dispatch",
			Event: &Event{
				Kind:     EventMention,
				Platform: PlatformCapabilities{Name: PlatformSlack},
			},
			HandlerIDs: []int64{mentionID},
		}, nil
	}

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
