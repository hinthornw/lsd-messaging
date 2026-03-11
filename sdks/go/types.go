package lsmsg

import "encoding/json"

// Platform represents a messaging platform.
type Platform string

const (
	PlatformSlack    Platform = "slack"
	PlatformTeams    Platform = "teams"
	PlatformDiscord  Platform = "discord"
	PlatformTelegram Platform = "telegram"
	PlatformGithub   Platform = "github"
	PlatformLinear   Platform = "linear"
	PlatformGchat    Platform = "gchat"
)

// EventKind represents the type of an incoming event.
type EventKind string

const (
	EventMessage  EventKind = "message"
	EventMention  EventKind = "mention"
	EventCommand  EventKind = "command"
	EventReaction EventKind = "reaction"
	EventRaw      EventKind = "raw"
)

// PlatformCapabilities describes what a platform supports.
type PlatformCapabilities struct {
	Name            Platform `json:"name"`
	Ephemeral       bool     `json:"ephemeral"`
	Threads         bool     `json:"threads"`
	Reactions       bool     `json:"reactions"`
	Streaming       bool     `json:"streaming"`
	Modals          bool     `json:"modals"`
	TypingIndicator bool     `json:"typing_indicator"`
}

// UserInfo holds information about the user who triggered an event.
type UserInfo struct {
	ID    string `json:"id"`
	Name  string `json:"name,omitempty"`
	Email string `json:"email,omitempty"`
}

// Event represents a parsed incoming event from any platform.
type Event struct {
	Kind         EventKind            `json:"kind"`
	Platform     PlatformCapabilities `json:"platform"`
	WorkspaceID  string               `json:"workspace_id"`
	ChannelID    string               `json:"channel_id"`
	ThreadID     string               `json:"thread_id"`
	MessageID    string               `json:"message_id"`
	User         UserInfo             `json:"user"`
	Text         string               `json:"text"`
	Command      string               `json:"command,omitempty"`
	Emoji        string               `json:"emoji,omitempty"`
	RawEventType string               `json:"raw_event_type,omitempty"`
	Raw          json.RawMessage      `json:"raw"`

	// bot is set internally so that Event methods can call back into the bot.
	bot *Bot
}

// RunResult holds the outcome of a LangGraph run.
type RunResult struct {
	ID     string          `json:"id"`
	Status string          `json:"status"`
	Output json.RawMessage `json:"output"`
}

// Text extracts the text content of the last message in the run output.
func (r *RunResult) Text() string {
	var out struct {
		Messages []struct {
			Content string `json:"content"`
		} `json:"messages"`
	}
	if err := json.Unmarshal(r.Output, &out); err != nil || len(out.Messages) == 0 {
		return ""
	}
	return out.Messages[len(out.Messages)-1].Content
}

// RunChunk represents a single chunk from a streamed run.
type RunChunk struct {
	Event     string          `json:"event"`
	Text      string          `json:"text"`
	TextDelta string          `json:"text_delta"`
	Data      json.RawMessage `json:"data"`
}

type webhookOutcome struct {
	Type       string  `json:"type"`
	StatusCode int     `json:"status_code,omitempty"`
	Error      string  `json:"error,omitempty"`
	Challenge  string  `json:"challenge,omitempty"`
	Event      *Event  `json:"event,omitempty"`
	HandlerIDs []int64 `json:"handler_ids,omitempty"`
}

// ffiBackend defines the interface for FFI calls, enabling mocking in tests.
type ffiBackend interface {
	SlackVerifySignature(signingSecret, timestamp, signature string, body []byte) bool
	SlackParseWebhook(body []byte, contentType string) (*Event, string, error)
	TeamsParseWebhook(payloadJSON []byte) (*Event, error)
	SlackStripMentions(text string) string
	TeamsStripMentions(text string) string
	DeterministicThreadID(platform, workspaceID, channelID, threadID string) string

	RegistryNew() int64
	RegistryFree(handle int64)
	RegistryRegister(handle int64, fieldsJSON []byte) int64
	RegistryMatchEvent(handle int64, eventJSON []byte) ([]int64, error)
	RegistryProcessSlackWebhook(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error)
	RegistryProcessTeamsWebhook(handle int64, body []byte) (*webhookOutcome, error)

	LangGraphNew(baseURL, apiKey string) int64
	LangGraphFree(handle int64)
	LangGraphCreateRun(handle int64, paramsJSON []byte) (string, error)
	LangGraphWaitRun(handle int64, threadID, runID string) (*RunResult, error)
	LangGraphCancelRun(handle int64, threadID, runID string) error
}
