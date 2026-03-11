package lsmsg

// #cgo LDFLAGS: -L${SRCDIR}/../../target/release -llsmsg_ffi
// #include "../../crates/lsmsg-ffi/include/lsmsg.h"
// #include <stdlib.h>
import "C"

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unsafe"
)

func init() {
	defaultBackend = cgoBackend{}
}

// cgoBackend is the real FFI implementation using cgo.
type cgoBackend struct{}

func cString(s string) *C.char { return C.CString(s) }

func cOptionalString(s string) *C.char {
	if s == "" {
		return nil
	}
	return C.CString(s)
}

func freeCString(cs *C.char) {
	if cs != nil {
		C.free(unsafe.Pointer(cs))
	}
}

func goStringFree(cs *C.char) string {
	s := C.GoString(cs)
	C.lsmsg_free_string(cs)
	return s
}

func parseWebhookOutcome(jsonStr string) (*webhookOutcome, error) {
	var outcome webhookOutcome
	if err := json.Unmarshal([]byte(jsonStr), &outcome); err != nil {
		return nil, fmt.Errorf("lsmsg: failed to parse webhook outcome: %w", err)
	}
	if outcome.Type == "" {
		return nil, errors.New("lsmsg: webhook outcome missing type")
	}
	return &outcome, nil
}

func (cgoBackend) SlackVerifySignature(signingSecret, timestamp, signature string, body []byte) bool {
	cSecret := cString(signingSecret)
	defer C.free(unsafe.Pointer(cSecret))
	cTs := cString(timestamp)
	defer C.free(unsafe.Pointer(cTs))
	cSig := cString(signature)
	defer C.free(unsafe.Pointer(cSig))

	var bodyPtr *C.uint8_t
	if len(body) > 0 {
		bodyPtr = (*C.uint8_t)(unsafe.Pointer(&body[0]))
	}
	return C.lsmsg_slack_verify_signature(cSecret, cTs, cSig, bodyPtr, C.size_t(len(body))) == 1
}

func (cgoBackend) SlackParseWebhook(body []byte, contentType string) (*Event, string, error) {
	var bodyPtr *C.uint8_t
	if len(body) > 0 {
		bodyPtr = (*C.uint8_t)(unsafe.Pointer(&body[0]))
	}
	cCT := cString(contentType)
	defer C.free(unsafe.Pointer(cCT))

	result := C.lsmsg_slack_parse_webhook(bodyPtr, C.size_t(len(body)), cCT)
	if result == nil {
		return nil, "", nil // ignored event
	}
	jsonStr := goStringFree(result)

	// The FFI returns a JSON envelope: {"event": ...} or {"challenge": "..."} or {"error": "..."}
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(jsonStr), &envelope); err != nil {
		return nil, "", fmt.Errorf("lsmsg: failed to parse webhook result: %w", err)
	}

	if raw, ok := envelope["error"]; ok {
		var msg string
		json.Unmarshal(raw, &msg)
		return nil, "", errors.New(msg)
	}
	if raw, ok := envelope["challenge"]; ok {
		var challenge string
		json.Unmarshal(raw, &challenge)
		return nil, challenge, nil
	}
	if raw, ok := envelope["event"]; ok {
		var ev Event
		if err := json.Unmarshal(raw, &ev); err != nil {
			return nil, "", fmt.Errorf("lsmsg: failed to parse event: %w", err)
		}
		return &ev, "", nil
	}
	return nil, "", nil
}

func (cgoBackend) TeamsParseWebhook(payloadJSON []byte) (*Event, error) {
	cPayload := cString(string(payloadJSON))
	defer C.free(unsafe.Pointer(cPayload))

	result := C.lsmsg_teams_parse_webhook(cPayload)
	if result == nil {
		return nil, nil
	}
	jsonStr := goStringFree(result)
	if strings.TrimSpace(jsonStr) == "null" {
		return nil, nil
	}

	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(jsonStr), &envelope); err == nil {
		if raw, ok := envelope["error"]; ok {
			var msg string
			json.Unmarshal(raw, &msg)
			return nil, errors.New(msg)
		}
		if raw, ok := envelope["event"]; ok {
			var ev Event
			if err := json.Unmarshal(raw, &ev); err != nil {
				return nil, fmt.Errorf("lsmsg: failed to parse event: %w", err)
			}
			return &ev, nil
		}
	}

	var ev Event
	if err := json.Unmarshal([]byte(jsonStr), &ev); err != nil {
		return nil, fmt.Errorf("lsmsg: failed to parse teams event: %w", err)
	}
	return &ev, nil
}

func (cgoBackend) SlackStripMentions(text string) string {
	cText := cString(text)
	defer C.free(unsafe.Pointer(cText))
	return goStringFree(C.lsmsg_slack_strip_mentions(cText))
}

func (cgoBackend) TeamsStripMentions(text string) string {
	cText := cString(text)
	defer C.free(unsafe.Pointer(cText))
	return goStringFree(C.lsmsg_teams_strip_mentions(cText))
}

func (cgoBackend) DeterministicThreadID(platform, workspaceID, channelID, threadID string) string {
	cP := cString(platform)
	defer C.free(unsafe.Pointer(cP))
	cW := cString(workspaceID)
	defer C.free(unsafe.Pointer(cW))
	cC := cString(channelID)
	defer C.free(unsafe.Pointer(cC))
	cT := cString(threadID)
	defer C.free(unsafe.Pointer(cT))
	return goStringFree(C.lsmsg_deterministic_thread_id(cP, cW, cC, cT))
}

func (cgoBackend) RegistryNew() int64 {
	return int64(C.lsmsg_registry_new())
}

func (cgoBackend) RegistryFree(handle int64) {
	C.lsmsg_registry_free(C.int64_t(handle))
}

func (cgoBackend) RegistryRegister(handle int64, fieldsJSON []byte) int64 {
	cFields := cString(string(fieldsJSON))
	defer C.free(unsafe.Pointer(cFields))
	return int64(C.lsmsg_registry_register(C.int64_t(handle), cFields))
}

func (cgoBackend) RegistryMatchEvent(handle int64, eventJSON []byte) ([]int64, error) {
	cEvent := cString(string(eventJSON))
	defer C.free(unsafe.Pointer(cEvent))
	result := C.lsmsg_registry_match_event(C.int64_t(handle), cEvent)
	if result == nil {
		return nil, nil
	}
	jsonStr := goStringFree(result)
	var ids []int64
	if err := json.Unmarshal([]byte(jsonStr), &ids); err != nil {
		return nil, fmt.Errorf("lsmsg: failed to parse match result: %w", err)
	}
	return ids, nil
}

func (cgoBackend) RegistryProcessSlackWebhook(handle int64, body []byte, contentType, signingSecret, timestamp, signature string) (*webhookOutcome, error) {
	var bodyPtr *C.uint8_t
	if len(body) > 0 {
		bodyPtr = (*C.uint8_t)(unsafe.Pointer(&body[0]))
	}

	cCT := cOptionalString(contentType)
	defer freeCString(cCT)
	cSecret := cOptionalString(signingSecret)
	defer freeCString(cSecret)
	cTs := cOptionalString(timestamp)
	defer freeCString(cTs)
	cSig := cOptionalString(signature)
	defer freeCString(cSig)

	result := C.lsmsg_registry_process_slack_webhook(
		C.int64_t(handle),
		bodyPtr,
		C.size_t(len(body)),
		cCT,
		cSecret,
		cTs,
		cSig,
	)
	if result == nil {
		return nil, errors.New("lsmsg: process_slack_webhook returned nil")
	}
	return parseWebhookOutcome(goStringFree(result))
}

func (cgoBackend) RegistryProcessTeamsWebhook(handle int64, body []byte) (*webhookOutcome, error) {
	var bodyPtr *C.uint8_t
	if len(body) > 0 {
		bodyPtr = (*C.uint8_t)(unsafe.Pointer(&body[0]))
	}

	result := C.lsmsg_registry_process_teams_webhook(C.int64_t(handle), bodyPtr, C.size_t(len(body)))
	if result == nil {
		return nil, errors.New("lsmsg: process_teams_webhook returned nil")
	}
	return parseWebhookOutcome(goStringFree(result))
}

func (cgoBackend) LangGraphNew(baseURL, apiKey string) int64 {
	cURL := cString(baseURL)
	defer C.free(unsafe.Pointer(cURL))
	cKey := cString(apiKey)
	defer C.free(unsafe.Pointer(cKey))
	return int64(C.lsmsg_langgraph_new(cURL, cKey))
}

func (cgoBackend) LangGraphFree(handle int64) {
	C.lsmsg_langgraph_free(C.int64_t(handle))
}

func (cgoBackend) LangGraphCreateRun(handle int64, paramsJSON []byte) (string, error) {
	cParams := cString(string(paramsJSON))
	defer C.free(unsafe.Pointer(cParams))
	result := C.lsmsg_langgraph_create_run(C.int64_t(handle), cParams)
	if result == nil {
		return "", errors.New("lsmsg: create_run returned nil")
	}
	jsonStr := goStringFree(result)

	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(jsonStr), &envelope); err != nil {
		return "", fmt.Errorf("lsmsg: failed to parse create_run result: %w", err)
	}
	if raw, ok := envelope["error"]; ok {
		var msg string
		json.Unmarshal(raw, &msg)
		return "", errors.New(msg)
	}
	if raw, ok := envelope["run_id"]; ok {
		var runID string
		json.Unmarshal(raw, &runID)
		return runID, nil
	}
	return "", errors.New("lsmsg: unexpected create_run response")
}

func (cgoBackend) LangGraphWaitRun(handle int64, threadID, runID string) (*RunResult, error) {
	cThread := cString(threadID)
	defer C.free(unsafe.Pointer(cThread))
	cRun := cString(runID)
	defer C.free(unsafe.Pointer(cRun))
	result := C.lsmsg_langgraph_wait_run(C.int64_t(handle), cThread, cRun)
	if result == nil {
		return nil, errors.New("lsmsg: wait_run returned nil")
	}
	jsonStr := goStringFree(result)

	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(jsonStr), &envelope); err != nil {
		return nil, fmt.Errorf("lsmsg: failed to parse wait_run result: %w", err)
	}
	if raw, ok := envelope["error"]; ok {
		var msg string
		json.Unmarshal(raw, &msg)
		return nil, errors.New(msg)
	}
	var rr RunResult
	if err := json.Unmarshal([]byte(jsonStr), &rr); err != nil {
		return nil, fmt.Errorf("lsmsg: failed to parse run result: %w", err)
	}
	return &rr, nil
}

func (cgoBackend) LangGraphCancelRun(handle int64, threadID, runID string) error {
	cThread := cString(threadID)
	defer C.free(unsafe.Pointer(cThread))
	cRun := cString(runID)
	defer C.free(unsafe.Pointer(cRun))
	result := C.lsmsg_langgraph_cancel_run(C.int64_t(handle), cThread, cRun)
	if result == nil {
		return nil
	}
	jsonStr := goStringFree(result)

	var envelope map[string]json.RawMessage
	if err := json.Unmarshal([]byte(jsonStr), &envelope); err != nil {
		return nil
	}
	if raw, ok := envelope["error"]; ok {
		var msg string
		json.Unmarshal(raw, &msg)
		return errors.New(msg)
	}
	return nil
}

// Public FFI wrapper functions that use the default cgo backend.

// SlackVerifySignature verifies a Slack request signature.
func SlackVerifySignature(signingSecret, timestamp, signature string, body []byte) bool {
	return cgoBackend{}.SlackVerifySignature(signingSecret, timestamp, signature, body)
}

// SlackParseWebhook parses a Slack webhook request body.
// Returns an event, a challenge string (for URL verification), or nil for ignored events.
func SlackParseWebhook(body []byte, contentType string) (*Event, string, error) {
	return cgoBackend{}.SlackParseWebhook(body, contentType)
}

// TeamsParseWebhook parses a Teams webhook payload.
func TeamsParseWebhook(payload map[string]any) (*Event, error) {
	data, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("lsmsg: failed to marshal payload: %w", err)
	}
	return cgoBackend{}.TeamsParseWebhook(data)
}

// SlackStripMentions removes Slack mention markup from text.
func SlackStripMentions(text string) string {
	return cgoBackend{}.SlackStripMentions(text)
}

// TeamsStripMentions removes Teams mention markup from text.
func TeamsStripMentions(text string) string {
	return cgoBackend{}.TeamsStripMentions(text)
}

// DeterministicThreadID produces a stable UUID v5 thread identifier.
func DeterministicThreadID(platform, workspaceID, channelID, threadID string) string {
	return cgoBackend{}.DeterministicThreadID(platform, workspaceID, channelID, threadID)
}
