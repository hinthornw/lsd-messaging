/// C FFI layer for lsmsg-core.
///
/// All complex types cross the boundary as JSON strings.
/// The caller must free returned strings with `lsmsg_free_string`.
use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

use std::sync::Mutex;

use serde_json::Value;

// ---------------------------------------------------------------------------
// Memory management
// ---------------------------------------------------------------------------

/// Free a string returned by any lsmsg_* function.
///
/// # Safety
///
/// `s` must be a pointer previously returned by an `lsmsg_*` function in this
/// library and must not have been freed already.
#[no_mangle]
pub unsafe extern "C" fn lsmsg_free_string(s: *mut c_char) {
    if !s.is_null() {
        drop(unsafe { CString::from_raw(s) });
    }
}

fn to_c_string(s: &str) -> *mut c_char {
    CString::new(s).unwrap_or_default().into_raw()
}

fn from_c_str(s: *const c_char) -> Option<String> {
    if s.is_null() {
        return None;
    }
    unsafe { CStr::from_ptr(s).to_str().ok().map(String::from) }
}

fn webhook_outcome_to_json(outcome: lsmsg_core::WebhookOutcome) -> String {
    match outcome {
        lsmsg_core::WebhookOutcome::Rejected {
            status_code,
            message,
        } => serde_json::json!({
            "type": "rejected",
            "status_code": status_code,
            "error": message,
        })
        .to_string(),
        lsmsg_core::WebhookOutcome::Challenge(challenge) => {
            serde_json::json!({ "type": "challenge", "challenge": challenge }).to_string()
        }
        lsmsg_core::WebhookOutcome::Ignored => serde_json::json!({ "type": "ignored" }).to_string(),
        lsmsg_core::WebhookOutcome::Dispatch(plan) => serde_json::json!({
            "type": "dispatch",
            "event": serde_json::to_value(&plan.event).unwrap_or(Value::Null),
            "handler_ids": plan.handler_ids,
        })
        .to_string(),
    }
}

// ---------------------------------------------------------------------------
// Slack
// ---------------------------------------------------------------------------

/// Verify a Slack request signature. Returns 1 for valid, 0 for invalid.
///
/// # Safety
///
/// `signing_secret`, `timestamp`, and `signature` must be valid NUL-terminated
/// strings when non-null. `body` must point to `body_len` readable bytes when
/// non-null.
#[no_mangle]
pub unsafe extern "C" fn lsmsg_slack_verify_signature(
    signing_secret: *const c_char,
    timestamp: *const c_char,
    signature: *const c_char,
    body: *const u8,
    body_len: usize,
) -> i32 {
    let secret = match from_c_str(signing_secret) {
        Some(s) => s,
        None => return 0,
    };
    let ts = match from_c_str(timestamp) {
        Some(s) => s,
        None => return 0,
    };
    let sig = match from_c_str(signature) {
        Some(s) => s,
        None => return 0,
    };
    if body.is_null() {
        return 0;
    }
    let body_slice = unsafe { std::slice::from_raw_parts(body, body_len) };
    if lsmsg_core::slack::verify_signature(&secret, &ts, &sig, body_slice) {
        1
    } else {
        0
    }
}

/// Parse a Slack webhook. Returns a JSON string (caller must free).
/// The JSON has { "type": "event"|"challenge"|"ignored", ... }.
///
/// # Safety
///
/// `content_type` must be a valid NUL-terminated string when non-null. `body`
/// must point to `body_len` readable bytes when non-null.
#[no_mangle]
pub unsafe extern "C" fn lsmsg_slack_parse_webhook(
    body: *const u8,
    body_len: usize,
    content_type: *const c_char,
) -> *mut c_char {
    let ct = from_c_str(content_type).unwrap_or_default();
    if body.is_null() {
        return to_c_string(r#"{"type":"ignored"}"#);
    }
    let body_slice = unsafe { std::slice::from_raw_parts(body, body_len) };
    let headers = HashMap::new();
    let result = lsmsg_core::slack::parse_webhook(body_slice, &ct, &headers);

    let json = match result {
        lsmsg_core::slack::SlackWebhookResult::Event(event) => {
            let event_val = serde_json::to_value(&event).unwrap_or(Value::Null);
            serde_json::json!({ "type": "event", "event": event_val })
        }
        lsmsg_core::slack::SlackWebhookResult::Challenge(c) => {
            serde_json::json!({ "type": "challenge", "challenge": c })
        }
        lsmsg_core::slack::SlackWebhookResult::Ignored => {
            serde_json::json!({ "type": "ignored" })
        }
    };
    to_c_string(&json.to_string())
}

/// Strip Slack mentions from text. Returns a new string (caller must free).
#[no_mangle]
pub extern "C" fn lsmsg_slack_strip_mentions(text: *const c_char) -> *mut c_char {
    let t = from_c_str(text).unwrap_or_default();
    to_c_string(&lsmsg_core::slack::strip_mentions(&t))
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

/// Parse a Teams webhook from a JSON string. Returns event JSON or "null".
/// Caller must free the result.
#[no_mangle]
pub extern "C" fn lsmsg_teams_parse_webhook(payload_json: *const c_char) -> *mut c_char {
    let json_str = from_c_str(payload_json).unwrap_or_default();
    let payload: Value = serde_json::from_str(&json_str).unwrap_or(Value::Null);
    match lsmsg_core::teams::parse_webhook(&payload) {
        Some(event) => {
            let val = serde_json::to_value(&event).unwrap_or(Value::Null);
            to_c_string(&val.to_string())
        }
        None => to_c_string("null"),
    }
}

/// Strip Teams mentions from text. Caller must free the result.
#[no_mangle]
pub extern "C" fn lsmsg_teams_strip_mentions(text: *const c_char) -> *mut c_char {
    let t = from_c_str(text).unwrap_or_default();
    to_c_string(&lsmsg_core::teams::strip_mentions(&t))
}

// ---------------------------------------------------------------------------
// Deterministic thread ID
// ---------------------------------------------------------------------------

/// Compute a deterministic thread ID. Caller must free the result.
#[no_mangle]
pub extern "C" fn lsmsg_deterministic_thread_id(
    platform: *const c_char,
    workspace_id: *const c_char,
    channel_id: *const c_char,
    thread_id: *const c_char,
) -> *mut c_char {
    let plat_str = from_c_str(platform).unwrap_or_default();
    let plat = match plat_str.as_str() {
        "slack" => lsmsg_core::Platform::Slack,
        "teams" => lsmsg_core::Platform::Teams,
        "discord" => lsmsg_core::Platform::Discord,
        "telegram" => lsmsg_core::Platform::Telegram,
        "github" => lsmsg_core::Platform::Github,
        "linear" => lsmsg_core::Platform::Linear,
        "gchat" => lsmsg_core::Platform::Gchat,
        _ => return to_c_string(""),
    };
    let ws = from_c_str(workspace_id).unwrap_or_default();
    let ch = from_c_str(channel_id).unwrap_or_default();
    let th = from_c_str(thread_id).unwrap_or_default();
    to_c_string(&lsmsg_core::deterministic_thread_id(&plat, &ws, &ch, &th))
}

// ---------------------------------------------------------------------------
// Handler Registry (opaque handle)
// ---------------------------------------------------------------------------

static REGISTRIES: Mutex<Vec<Option<lsmsg_core::HandlerRegistry>>> = Mutex::new(Vec::new());

/// Create a new handler registry. Returns a handle (>= 0) or -1 on error.
#[no_mangle]
pub extern "C" fn lsmsg_registry_new() -> i64 {
    let mut regs = REGISTRIES.lock().unwrap();
    let idx = regs.len();
    regs.push(Some(lsmsg_core::HandlerRegistry::new()));
    idx as i64
}

/// Free a handler registry.
#[no_mangle]
pub extern "C" fn lsmsg_registry_free(handle: i64) {
    let mut regs = REGISTRIES.lock().unwrap();
    if let Some(slot) = regs.get_mut(handle as usize) {
        *slot = None;
    }
}

/// Register a handler filter. fields_json is a JSON object with optional keys:
/// event_kind, command, pattern, emoji, platform, raw_event_type.
/// Returns handler ID (> 0) or -1 on error.
#[no_mangle]
pub extern "C" fn lsmsg_registry_register(handle: i64, fields_json: *const c_char) -> i64 {
    let json_str = from_c_str(fields_json).unwrap_or_default();
    let fields: Value = serde_json::from_str(&json_str).unwrap_or(Value::Null);

    let mut regs = REGISTRIES.lock().unwrap();
    let reg = match regs.get_mut(handle as usize).and_then(|s| s.as_mut()) {
        Some(r) => r,
        None => return -1,
    };

    let event_kind = fields
        .get("event_kind")
        .and_then(|v| v.as_str())
        .map(|s| match s {
            "message" => lsmsg_core::EventKind::Message,
            "mention" => lsmsg_core::EventKind::Mention,
            "command" => lsmsg_core::EventKind::Command,
            "reaction" => lsmsg_core::EventKind::Reaction,
            _ => lsmsg_core::EventKind::Raw,
        });
    let command = fields
        .get("command")
        .and_then(|v| v.as_str())
        .map(String::from);
    let pattern = fields.get("pattern").and_then(|v| v.as_str());
    let emoji = fields
        .get("emoji")
        .and_then(|v| v.as_str())
        .map(String::from);
    let platform = fields
        .get("platform")
        .and_then(|v| v.as_str())
        .map(|s| match s {
            "slack" => lsmsg_core::Platform::Slack,
            "teams" => lsmsg_core::Platform::Teams,
            "discord" => lsmsg_core::Platform::Discord,
            "telegram" => lsmsg_core::Platform::Telegram,
            "github" => lsmsg_core::Platform::Github,
            "linear" => lsmsg_core::Platform::Linear,
            _ => lsmsg_core::Platform::Gchat,
        });
    let raw_event_type = fields
        .get("raw_event_type")
        .and_then(|v| v.as_str())
        .map(String::from);

    match reg.register_from_fields(
        event_kind,
        command,
        pattern,
        emoji,
        platform,
        raw_event_type,
    ) {
        Ok(id) => id as i64,
        Err(_) => -1,
    }
}

/// Match an event against a registry. Returns a JSON array of handler IDs.
/// Caller must free the result.
#[no_mangle]
pub extern "C" fn lsmsg_registry_match_event(
    handle: i64,
    event_json: *const c_char,
) -> *mut c_char {
    let json_str = from_c_str(event_json).unwrap_or_default();
    let event: lsmsg_core::Event = match serde_json::from_str(&json_str) {
        Ok(e) => e,
        Err(_) => return to_c_string("[]"),
    };

    let regs = REGISTRIES.lock().unwrap();
    let reg = match regs.get(handle as usize).and_then(|s| s.as_ref()) {
        Some(r) => r,
        None => return to_c_string("[]"),
    };

    let ids = reg.match_event(&event);
    let val = serde_json::to_value(&ids).unwrap_or(Value::Array(vec![]));
    to_c_string(&val.to_string())
}

/// Process a Slack webhook against a registry and return a JSON outcome.
///
/// # Safety
///
/// `content_type`, `signing_secret`, `timestamp`, and `signature` must be valid
/// NUL-terminated strings when non-null. `body` must point to `body_len`
/// readable bytes when non-null.
#[no_mangle]
pub unsafe extern "C" fn lsmsg_registry_process_slack_webhook(
    handle: i64,
    body: *const u8,
    body_len: usize,
    content_type: *const c_char,
    signing_secret: *const c_char,
    timestamp: *const c_char,
    signature: *const c_char,
) -> *mut c_char {
    let ct = from_c_str(content_type).unwrap_or_else(|| "application/json".into());
    let secret = from_c_str(signing_secret);
    let ts = from_c_str(timestamp);
    let sig = from_c_str(signature);

    if body.is_null() {
        return to_c_string(&webhook_outcome_to_json(
            lsmsg_core::WebhookOutcome::Rejected {
                status_code: 400,
                message: "empty body".into(),
            },
        ));
    }
    let body_slice = unsafe { std::slice::from_raw_parts(body, body_len) };

    let regs = REGISTRIES.lock().unwrap();
    let reg = match regs.get(handle as usize).and_then(|s| s.as_ref()) {
        Some(r) => r,
        None => {
            return to_c_string(&webhook_outcome_to_json(
                lsmsg_core::WebhookOutcome::Rejected {
                    status_code: 500,
                    message: "invalid registry handle".into(),
                },
            ))
        }
    };

    let outcome = lsmsg_core::process_slack_webhook(
        body_slice,
        &ct,
        secret.as_deref(),
        ts.as_deref(),
        sig.as_deref(),
        reg,
    );
    to_c_string(&webhook_outcome_to_json(outcome))
}

/// Process a Teams webhook against a registry and return a JSON outcome.
///
/// # Safety
///
/// `body` must point to `body_len` readable bytes when non-null.
#[no_mangle]
pub unsafe extern "C" fn lsmsg_registry_process_teams_webhook(
    handle: i64,
    body: *const u8,
    body_len: usize,
) -> *mut c_char {
    if body.is_null() {
        return to_c_string(&webhook_outcome_to_json(
            lsmsg_core::WebhookOutcome::Rejected {
                status_code: 400,
                message: "empty body".into(),
            },
        ));
    }
    let body_slice = unsafe { std::slice::from_raw_parts(body, body_len) };

    let regs = REGISTRIES.lock().unwrap();
    let reg = match regs.get(handle as usize).and_then(|s| s.as_ref()) {
        Some(r) => r,
        None => {
            return to_c_string(&webhook_outcome_to_json(
                lsmsg_core::WebhookOutcome::Rejected {
                    status_code: 500,
                    message: "invalid registry handle".into(),
                },
            ))
        }
    };

    let outcome = lsmsg_core::process_teams_webhook(body_slice, reg);
    to_c_string(&webhook_outcome_to_json(outcome))
}
