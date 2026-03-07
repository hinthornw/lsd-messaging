//go:build integration

package lsmsg

import (
	"testing"
)

// Integration tests require the built shared library (cargo build --release).
// Run with: go test -tags integration ./...

func TestFFISlackVerifySignature(t *testing.T) {
	// A valid signature would require computing HMAC-SHA256. This test just
	// verifies the FFI call doesn't panic with an invalid signature.
	result := SlackVerifySignature("secret", "1234567890", "v0=invalid", []byte("body"))
	if result {
		t.Error("expected false for invalid signature")
	}
}

func TestFFIDeterministicThreadID(t *testing.T) {
	id := DeterministicThreadID("slack", "T1", "C1", "t1")
	if id == "" {
		t.Fatal("expected non-empty thread ID")
	}

	// Same inputs should produce the same ID.
	id2 := DeterministicThreadID("slack", "T1", "C1", "t1")
	if id != id2 {
		t.Errorf("expected deterministic result, got %s and %s", id, id2)
	}

	// Different inputs should produce different IDs.
	id3 := DeterministicThreadID("teams", "T1", "C1", "t1")
	if id == id3 {
		t.Error("expected different IDs for different platforms")
	}
}

func TestFFISlackStripMentions(t *testing.T) {
	result := SlackStripMentions("<@U123> hello world")
	if result == "<@U123> hello world" {
		t.Error("expected mentions to be stripped")
	}
}

func TestFFITeamsStripMentions(t *testing.T) {
	result := TeamsStripMentions("<at>Bot</at> hello world")
	if result == "<at>Bot</at> hello world" {
		t.Error("expected mentions to be stripped")
	}
}

func TestFFITeamsParseWebhook(t *testing.T) {
	event, err := TeamsParseWebhook(map[string]any{
		"type": "message",
		"text": "hello teams",
		"from": map[string]any{
			"id":   "U1",
			"name": "Alice",
		},
		"conversation": map[string]any{
			"id":       "conv-1",
			"tenantId": "tenant-1",
		},
		"channelData": map[string]any{
			"tenant": map[string]any{"id": "tenant-1"},
			"team":   map[string]any{"id": "team-1"},
		},
		"id": "msg-1",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if event == nil {
		t.Fatal("expected event")
	}
	if event.Kind != EventMessage {
		t.Fatalf("expected message event, got %s", event.Kind)
	}
	if event.Text != "hello teams" {
		t.Fatalf("expected text to round-trip, got %q", event.Text)
	}
}
