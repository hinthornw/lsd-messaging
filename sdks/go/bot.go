package lsmsg

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
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

// handlerEntry stores a registered handler along with its filter.
type handlerEntry struct {
	id           int64
	kind         EventKind
	command      string
	emoji        string
	platform     Platform
	rawEventType string
	pattern      *regexp.Regexp
	handler      EventHandler
}

// Bot is the main entry point for handling messaging platform webhooks.
// It implements http.Handler.
type Bot struct {
	config   BotConfig
	ffi      ffiBackend
	mu       sync.RWMutex
	handlers []handlerEntry
	nextID   int64

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
	b := &Bot{
		config:              config,
		ffi:                 backend,
		nextID:              1,
		langGraphConfigured: config.LangGraph != nil,
		httpClient:          http.DefaultClient,
		slackAPIBaseURL:     "https://slack.com",
	}
	if config.LangGraph != nil {
		b.lgHandle = b.ffi.LangGraphNew(config.LangGraph.URL, config.LangGraph.APIKey)
	}
	return b
}

func (b *Bot) register(kind EventKind, command, emoji string, platform Platform, rawEventType string, pattern *regexp.Regexp, handler EventHandler) {
	b.mu.Lock()
	defer b.mu.Unlock()
	id := b.nextID
	b.nextID++
	b.handlers = append(b.handlers, handlerEntry{
		id:           id,
		kind:         kind,
		command:      command,
		emoji:        emoji,
		platform:     platform,
		rawEventType: rawEventType,
		pattern:      pattern,
		handler:      handler,
	})
}

// OnMention registers a handler for mention events.
func (b *Bot) OnMention(handler EventHandler) {
	b.register(EventMention, "", "", "", "", nil, handler)
}

// OnMentionWithOpts registers a mention handler with additional filters.
func (b *Bot) OnMentionWithOpts(handler EventHandler, opts HandlerOpts) {
	var pat *regexp.Regexp
	if opts.Pattern != "" {
		pat = regexp.MustCompile(opts.Pattern)
	}
	b.register(EventMention, "", "", opts.Platform, "", pat, handler)
}

// OnMessage registers a handler for message events.
func (b *Bot) OnMessage(handler EventHandler) {
	b.register(EventMessage, "", "", "", "", nil, handler)
}

// OnMessageWithOpts registers a message handler with additional filters.
func (b *Bot) OnMessageWithOpts(handler EventHandler, opts HandlerOpts) {
	var pat *regexp.Regexp
	if opts.Pattern != "" {
		pat = regexp.MustCompile(opts.Pattern)
	}
	b.register(EventMessage, "", "", opts.Platform, "", pat, handler)
}

// Command registers a handler for a slash command.
func (b *Bot) Command(name string, handler EventHandler) {
	b.register(EventCommand, name, "", "", "", nil, handler)
}

// CommandWithOpts registers a command handler with additional filters.
func (b *Bot) CommandWithOpts(name string, handler EventHandler, opts HandlerOpts) {
	var pat *regexp.Regexp
	if opts.Pattern != "" {
		pat = regexp.MustCompile(opts.Pattern)
	}
	b.register(EventCommand, name, "", opts.Platform, "", pat, handler)
}

// OnReaction registers a handler for a specific emoji reaction.
func (b *Bot) OnReaction(emoji string, handler EventHandler) {
	b.register(EventReaction, "", emoji, "", "", nil, handler)
}

// OnReactionWithOpts registers a reaction handler with additional filters.
func (b *Bot) OnReactionWithOpts(emoji string, handler EventHandler, opts HandlerOpts) {
	var pat *regexp.Regexp
	if opts.Pattern != "" {
		pat = regexp.MustCompile(opts.Pattern)
	}
	b.register(EventReaction, "", emoji, opts.Platform, "", pat, handler)
}

// On registers a handler for a raw event type.
func (b *Bot) On(eventType string, handler EventHandler) {
	b.register(EventRaw, "", "", "", eventType, nil, handler)
}

// OnWithOpts registers a raw event handler with additional filters.
func (b *Bot) OnWithOpts(eventType string, handler EventHandler, opts HandlerOpts) {
	var pat *regexp.Regexp
	if opts.Pattern != "" {
		pat = regexp.MustCompile(opts.Pattern)
	}
	b.register(EventRaw, "", "", opts.Platform, eventType, pat, handler)
}

// matchHandlers returns all handlers matching the given event.
func (b *Bot) matchHandlers(event *Event) []handlerEntry {
	b.mu.RLock()
	defer b.mu.RUnlock()

	var matched []handlerEntry
	for _, h := range b.handlers {
		if !handlerMatches(&h, event) {
			continue
		}
		matched = append(matched, h)
	}
	return matched
}

func handlerMatches(h *handlerEntry, event *Event) bool {
	if h.kind != "" && h.kind != event.Kind {
		return false
	}
	if h.platform != "" && h.platform != event.Platform.Name {
		return false
	}
	if h.command != "" && h.command != event.Command {
		return false
	}
	if h.emoji != "" && h.emoji != event.Emoji {
		return false
	}
	if h.rawEventType != "" && h.rawEventType != event.RawEventType {
		return false
	}
	if h.pattern != nil && !h.pattern.MatchString(event.Text) {
		return false
	}
	return true
}

// dispatch runs all matching handlers for an event.
func (b *Bot) dispatch(event *Event) error {
	event.bot = b
	handlers := b.matchHandlers(event)
	var errs []error
	for _, h := range handlers {
		if err := h.handler(event); err != nil {
			errs = append(errs, err)
		}
	}
	return errors.Join(errs...)
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

// HandleSlackWebhook handles an incoming Slack webhook HTTP request.
func (b *Bot) HandleSlackWebhook(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()

	body, err := io.ReadAll(io.LimitReader(r.Body, int64(b.config.MaxBodyBytes)))
	if err != nil {
		http.Error(w, "failed to read body", http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	// Verify signature if configured.
	if b.config.Slack != nil && b.config.Slack.SigningSecret != "" {
		ts := r.Header.Get("X-Slack-Request-Timestamp")
		sig := r.Header.Get("X-Slack-Signature")
		if !b.ffi.SlackVerifySignature(b.config.Slack.SigningSecret, ts, sig, body) {
			http.Error(w, "invalid signature", http.StatusUnauthorized)
			return
		}
	}

	event, challenge, err := b.ffi.SlackParseWebhook(body, r.Header.Get("Content-Type"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// URL verification challenge.
	if challenge != "" {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"challenge": challenge})
		return
	}

	// Ignored event.
	if event == nil {
		w.WriteHeader(http.StatusOK)
		return
	}

	b.dispatchAsync(ctx, event, w)
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

	event, err := b.ffi.TeamsParseWebhook(body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	if event == nil {
		w.WriteHeader(http.StatusOK)
		return
	}

	b.dispatchAsync(ctx, event, w)
}

func (b *Bot) dispatchAsync(_ context.Context, event *Event, w http.ResponseWriter) {
	// Acknowledge immediately, dispatch in background.
	if err := b.dispatch(event); err != nil {
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
