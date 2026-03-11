package lsmsg

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"runtime"
	"strings"
	"sync"
)

// EventHandler is the callback signature for event handlers.
type EventHandler func(event *Event) error

// HandlerOpts specifies optional filters for handler registration.
type HandlerOpts struct {
	Pattern  string
	Platform Platform
}

// InvokeOpt configures an Invoke call.
type InvokeOpt func(*invokeConfig)

type invokeConfig struct {
	input    map[string]any
	config   map[string]any
	metadata map[string]any
}

// WithInput sets the input for a LangGraph run.
func WithInput(input map[string]any) InvokeOpt {
	return func(c *invokeConfig) { c.input = input }
}

// WithConfig sets the config for a LangGraph run.
func WithConfig(config map[string]any) InvokeOpt {
	return func(c *invokeConfig) { c.config = config }
}

// WithMetadata sets the metadata for a LangGraph run.
func WithMetadata(metadata map[string]any) InvokeOpt {
	return func(c *invokeConfig) { c.metadata = metadata }
}

// BotConfig holds configuration for creating a new Bot.
type BotConfig struct {
	Slack           *SlackConfig
	Teams           *TeamsConfig
	LangGraph       *LangGraphConfig
	MaxPendingTasks int
	MaxBodyBytes    int
}

// SlackConfig holds Slack-specific configuration.
type SlackConfig struct {
	SigningSecret string
	BotToken      string
}

// TeamsConfig holds Teams-specific configuration.
type TeamsConfig struct {
	AppID       string
	AppPassword string
}

// LangGraphConfig holds LangGraph client configuration.
type LangGraphConfig struct {
	URL    string
	APIKey string
}

// Bot is the main entry point for handling messaging platform webhooks.
// It implements http.Handler.
type Bot struct {
	config         BotConfig
	ffi            ffiBackend
	mu             sync.RWMutex
	handlers       map[int64]EventHandler
	registryHandle int64
	closed         bool

	// lgHandle is the FFI handle for the LangGraph client. Handle 0 is valid.
	lgHandle int64

	langGraphConfigured bool
	httpClient          *http.Client
	slackAPIBaseURL     string
}

// defaultBackend is set by the cgo init function. It is nil when the shared
// library is not linked (e.g. in pure-Go unit tests).
var defaultBackend ffiBackend

// NewBot creates a new Bot with the given configuration.
// It panics if the FFI shared library was not linked. Use newBotWithBackend
// in tests to supply a mock backend.
func NewBot(config BotConfig) *Bot {
	if defaultBackend == nil {
		panic("lsmsg: FFI backend not available — build with CGO_ENABLED=1 and the lsmsg_ffi shared library")
	}
	return newBotWithBackend(config, defaultBackend)
}

// newBotWithBackend creates a Bot with a custom FFI backend (for testing).
func newBotWithBackend(config BotConfig, backend ffiBackend) *Bot {
	if config.MaxBodyBytes == 0 {
		config.MaxBodyBytes = 1 << 20
	}
	if config.MaxPendingTasks == 0 {
		config.MaxPendingTasks = 100
	}
	registryHandle := backend.RegistryNew()
	if registryHandle < 0 {
		panic("lsmsg: failed to create handler registry")
	}
	b := &Bot{
		config:              config,
		ffi:                 backend,
		handlers:            make(map[int64]EventHandler),
		registryHandle:      registryHandle,
		langGraphConfigured: config.LangGraph != nil,
		httpClient:          http.DefaultClient,
		slackAPIBaseURL:     "https://slack.com",
	}
	if config.LangGraph != nil {
		b.lgHandle = b.ffi.LangGraphNew(config.LangGraph.URL, config.LangGraph.APIKey)
	}
	runtime.SetFinalizer(b, (*Bot).Close)
	return b
}

// Close releases native resources owned by the bot. It is safe to call multiple times.
func (b *Bot) Close() {
	runtime.SetFinalizer(b, nil)

	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return
	}
	b.closed = true
	registryHandle := b.registryHandle
	lgHandle := b.lgHandle
	freeLangGraph := b.langGraphConfigured
	b.mu.Unlock()

	b.ffi.RegistryFree(registryHandle)
	if freeLangGraph {
		b.ffi.LangGraphFree(lgHandle)
	}
}

func (b *Bot) register(kind EventKind, command, emoji string, platform Platform, rawEventType string, pattern string, handler EventHandler) {
	fields := map[string]string{}
	if kind != "" {
		fields["event_kind"] = string(kind)
	}
	if command != "" {
		fields["command"] = command
	}
	if emoji != "" {
		fields["emoji"] = emoji
	}
	if platform != "" {
		fields["platform"] = string(platform)
	}
	if rawEventType != "" {
		fields["raw_event_type"] = rawEventType
	}
	if pattern != "" {
		fields["pattern"] = pattern
	}

	fieldsJSON, err := json.Marshal(fields)
	if err != nil {
		panic(fmt.Sprintf("lsmsg: failed to marshal handler filter: %v", err))
	}
	id := b.ffi.RegistryRegister(b.registryHandle, fieldsJSON)
	if id < 0 {
		panic("lsmsg: failed to register handler filter")
	}

	b.mu.Lock()
	defer b.mu.Unlock()
	b.handlers[id] = handler
}

// OnMention registers a handler for mention events.
func (b *Bot) OnMention(handler EventHandler) {
	b.register(EventMention, "", "", "", "", "", handler)
}

// OnMentionWithOpts registers a mention handler with additional filters.
func (b *Bot) OnMentionWithOpts(handler EventHandler, opts HandlerOpts) {
	b.register(EventMention, "", "", opts.Platform, "", opts.Pattern, handler)
}

// OnMessage registers a handler for message events.
func (b *Bot) OnMessage(handler EventHandler) {
	b.register(EventMessage, "", "", "", "", "", handler)
}

// OnMessageWithOpts registers a message handler with additional filters.
func (b *Bot) OnMessageWithOpts(handler EventHandler, opts HandlerOpts) {
	b.register(EventMessage, "", "", opts.Platform, "", opts.Pattern, handler)
}

// Command registers a handler for a slash command.
func (b *Bot) Command(name string, handler EventHandler) {
	b.register(EventCommand, name, "", "", "", "", handler)
}

// CommandWithOpts registers a command handler with additional filters.
func (b *Bot) CommandWithOpts(name string, handler EventHandler, opts HandlerOpts) {
	b.register(EventCommand, name, "", opts.Platform, "", opts.Pattern, handler)
}

// OnReaction registers a handler for a specific emoji reaction.
func (b *Bot) OnReaction(emoji string, handler EventHandler) {
	b.register(EventReaction, "", emoji, "", "", "", handler)
}

// OnReactionWithOpts registers a reaction handler with additional filters.
func (b *Bot) OnReactionWithOpts(emoji string, handler EventHandler, opts HandlerOpts) {
	b.register(EventReaction, "", emoji, opts.Platform, "", opts.Pattern, handler)
}

// On registers a handler for a raw event type.
func (b *Bot) On(eventType string, handler EventHandler) {
	b.register(EventRaw, "", "", "", eventType, "", handler)
}

// OnWithOpts registers a raw event handler with additional filters.
func (b *Bot) OnWithOpts(eventType string, handler EventHandler, opts HandlerOpts) {
	b.register(EventRaw, "", "", opts.Platform, eventType, opts.Pattern, handler)
}

func (b *Bot) matchHandlerIDs(event *Event) ([]int64, error) {
	eventJSON, err := json.Marshal(event)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: failed to marshal event: %w", err)
	}
	ids, err := b.ffi.RegistryMatchEvent(b.registryHandle, eventJSON)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: failed to match event: %w", err)
	}
	return ids, nil
}

func (b *Bot) dispatchMatched(event *Event, handlerIDs []int64) error {
	event.bot = b

	b.mu.RLock()
	callbacks := make([]EventHandler, 0, len(handlerIDs))
	var errs []error
	for _, id := range handlerIDs {
		handler, ok := b.handlers[id]
		if !ok {
			errs = append(errs, fmt.Errorf("lsmsg: handler %d not found", id))
			continue
		}
		callbacks = append(callbacks, handler)
	}
	b.mu.RUnlock()

	for _, handler := range callbacks {
		if err := handler(event); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
}

// dispatch runs all matching handlers for an event.
func (b *Bot) dispatch(event *Event) error {
	handlerIDs, err := b.matchHandlerIDs(event)
	if err != nil {
		return err
	}
	return b.dispatchMatched(event, handlerIDs)
}

// ServeHTTP implements http.Handler. It routes requests to the appropriate
// platform webhook handler based on the URL path.
func (b *Bot) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	switch r.URL.Path {
	case "/slack/events", "/slack":
		b.HandleSlackWebhook(w, r)
	case "/teams/events", "/teams":
		b.HandleTeamsWebhook(w, r)
	default:
		// Try to auto-detect based on headers.
		if r.Header.Get("X-Slack-Signature") != "" {
			b.HandleSlackWebhook(w, r)
			return
		}
		// Default to a generic handler that tries both.
		http.Error(w, "unknown webhook path", http.StatusNotFound)
	}
}

func (b *Bot) writeWebhookOutcome(ctx context.Context, w http.ResponseWriter, outcome *webhookOutcome) {
	if outcome == nil {
		http.Error(w, "failed to process webhook", http.StatusInternalServerError)
		return
	}

	switch outcome.Type {
	case "rejected":
		statusCode := outcome.StatusCode
		if statusCode == 0 {
			statusCode = http.StatusBadRequest
		}
		message := outcome.Error
		if message == "" {
			message = http.StatusText(statusCode)
		}
		http.Error(w, message, statusCode)
	case "challenge":
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{"challenge": outcome.Challenge})
	case "ignored":
		w.WriteHeader(http.StatusOK)
	case "dispatch":
		if outcome.Event == nil {
			http.Error(w, "failed to process webhook", http.StatusInternalServerError)
			return
		}
		b.dispatchAsync(ctx, outcome.Event, outcome.HandlerIDs, w)
	default:
		http.Error(w, "failed to process webhook", http.StatusInternalServerError)
	}
}

// HandleSlackWebhook handles an incoming Slack webhook HTTP request.
func (b *Bot) HandleSlackWebhook(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	body, err := io.ReadAll(io.LimitReader(r.Body, int64(b.config.MaxBodyBytes)))
	if err != nil {
		http.Error(w, "failed to read body", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	signingSecret := ""
	if b.config.Slack != nil {
		signingSecret = b.config.Slack.SigningSecret
	}
	outcome, err := b.ffi.RegistryProcessSlackWebhook(
		b.registryHandle,
		body,
		r.Header.Get("Content-Type"),
		signingSecret,
		r.Header.Get("X-Slack-Request-Timestamp"),
		r.Header.Get("X-Slack-Signature"),
	)
	if err != nil {
		http.Error(w, "failed to process webhook", http.StatusInternalServerError)
		return
	}
	b.writeWebhookOutcome(ctx, w, outcome)
}

// HandleTeamsWebhook handles an incoming Teams webhook HTTP request.
func (b *Bot) HandleTeamsWebhook(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	body, err := io.ReadAll(io.LimitReader(r.Body, int64(b.config.MaxBodyBytes)))
	if err != nil {
		http.Error(w, "failed to read body", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	outcome, err := b.ffi.RegistryProcessTeamsWebhook(b.registryHandle, body)
	if err != nil {
		http.Error(w, "failed to process webhook", http.StatusInternalServerError)
		return
	}
	b.writeWebhookOutcome(ctx, w, outcome)
}

func (b *Bot) dispatchAsync(_ context.Context, event *Event, handlerIDs []int64, w http.ResponseWriter) {
	// Acknowledge immediately, dispatch in background.
	if err := b.dispatchMatched(event, handlerIDs); err != nil {
		// Log but still return 200 to the platform.
		_ = err
	}
	w.WriteHeader(http.StatusOK)
}

// Invoke creates a LangGraph run and waits for its result.
func (e *Event) Invoke(agent string, opts ...InvokeOpt) (*RunResult, error) {
	if e.bot == nil {
		return nil, errors.New("lsmsg: event not associated with a bot")
	}
	if !e.bot.langGraphConfigured {
		return nil, errors.New("lsmsg: LangGraph not configured")
	}

	cfg := &invokeConfig{}
	for _, o := range opts {
		o(cfg)
	}

	threadID := e.bot.ffi.DeterministicThreadID(
		string(e.Platform.Name),
		e.WorkspaceID,
		e.ChannelID,
		e.ThreadID,
	)

	input := cfg.input
	if input == nil {
		input = map[string]any{
			"messages": []map[string]any{
				{"role": "user", "content": e.Text},
			},
		}
	}

	params := map[string]any{
		"agent":     agent,
		"thread_id": threadID,
	}
	if input != nil {
		params["input"] = input
	}
	if cfg.config != nil {
		params["config"] = cfg.config
	}
	if cfg.metadata != nil {
		params["metadata"] = cfg.metadata
	}

	paramsJSON, err := json.Marshal(params)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: failed to marshal params: %w", err)
	}

	runID, err := e.bot.ffi.LangGraphCreateRun(e.bot.lgHandle, paramsJSON)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: create run failed: %w", err)
	}

	result, err := e.bot.ffi.LangGraphWaitRun(e.bot.lgHandle, threadID, runID)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: wait run failed: %w", err)
	}

	return result, nil
}

// Reply sends a text reply back to the originating platform channel/thread.
// For Slack this uses chat.postMessage; for Teams it uses the activity reply API.
func (e *Event) Reply(text string) error {
	if e.bot == nil {
		return errors.New("lsmsg: event not associated with a bot")
	}

	switch e.Platform.Name {
	case PlatformSlack:
		return e.replySlack(text)
	case PlatformTeams:
		return e.replyTeams(text)
	default:
		return fmt.Errorf("lsmsg: reply not implemented for platform %s", e.Platform.Name)
	}
}

func (e *Event) replySlack(text string) error {
	if e.bot.config.Slack == nil || e.bot.config.Slack.BotToken == "" {
		return errors.New("lsmsg: Slack bot token not configured")
	}

	payload := map[string]string{
		"channel":   e.ChannelID,
		"text":      text,
		"thread_ts": e.ThreadID,
	}
	body, _ := json.Marshal(payload)

	url := strings.TrimRight(e.bot.slackAPIBaseURL, "/") + "/api/chat.postMessage"
	req, err := http.NewRequest(http.MethodPost, url, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	req.Header.Set("Authorization", "Bearer "+e.bot.config.Slack.BotToken)
	req.Body = io.NopCloser(jsonReader(body))

	resp, err := e.bot.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("lsmsg: slack reply failed: %w", err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("lsmsg: slack reply returned %d: %s", resp.StatusCode, string(respBody))
	}

	var result struct {
		OK    bool   `json:"ok"`
		Error string `json:"error"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return fmt.Errorf("lsmsg: slack reply returned invalid JSON: %w", err)
	}
	if !result.OK {
		if result.Error == "" {
			result.Error = "unknown_error"
		}
		return fmt.Errorf("lsmsg: slack reply failed: %s", result.Error)
	}
	return nil
}

func (e *Event) replyTeams(_ string) error {
	// Teams reply requires the service URL from the original activity,
	// which would be stored in the raw event. Placeholder for now.
	return errors.New("lsmsg: Teams reply not yet implemented")
}

// jsonReader wraps a byte slice as an io.Reader.
func jsonReader(b []byte) io.Reader {
	return &jsonBytesReader{data: b}
}

type jsonBytesReader struct {
	data []byte
	pos  int
}

func (r *jsonBytesReader) Read(p []byte) (int, error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}
