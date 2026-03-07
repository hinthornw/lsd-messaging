use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use regex::Regex;
use serde_json::Value;
use sha2::Sha256;
use subtle::ConstantTimeEq;

use crate::event::{Event, EventKind, UserInfo};
use crate::platform;

/// Result of parsing a Slack webhook payload.
pub enum SlackWebhookResult {
    /// A normalized event ready for dispatch.
    Event(Event),
    /// A url_verification challenge response.
    Challenge(String),
    /// The payload should be ignored (bot messages, unknown types, etc.).
    Ignored,
}

/// Verify a Slack request signature using HMAC-SHA256.
pub fn verify_signature(
    signing_secret: &str,
    timestamp: &str,
    signature: &str,
    body: &[u8],
) -> bool {
    let ts: i64 = match timestamp.parse() {
        Ok(v) => v,
        Err(_) => return false,
    };

    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;

    if (now - ts).unsigned_abs() > 60 * 5 {
        return false;
    }

    let mut mac =
        Hmac::<Sha256>::new_from_slice(signing_secret.as_bytes()).expect("HMAC accepts any key");
    mac.update(b"v0:");
    mac.update(timestamp.as_bytes());
    mac.update(b":");
    mac.update(body);
    let computed = hex::encode(mac.finalize().into_bytes());
    let expected = format!("v0={computed}");

    expected.as_bytes().ct_eq(signature.as_bytes()).into()
}

/// Parse a Slack webhook body into a `SlackWebhookResult`.
///
/// `content_type` should be the value of the Content-Type header.
/// `headers` is used only for metadata passthrough (not for auth — call
/// `verify_signature` separately).
pub fn parse_webhook(
    body: &[u8],
    content_type: &str,
    _headers: &HashMap<String, String>,
) -> SlackWebhookResult {
    if content_type.starts_with("application/x-www-form-urlencoded") {
        return parse_form(body);
    }

    let payload: Value = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(_) => return SlackWebhookResult::Ignored,
    };

    if payload.get("type").and_then(|v| v.as_str()) == Some("url_verification") {
        let challenge = payload
            .get("challenge")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        return SlackWebhookResult::Challenge(challenge);
    }

    parse_event_callback(&payload)
}

fn parse_form(body: &[u8]) -> SlackWebhookResult {
    let decoded = match std::str::from_utf8(body) {
        Ok(s) => s,
        Err(_) => return SlackWebhookResult::Ignored,
    };

    let form = parse_query_string(decoded);

    // Interactive payload (block_actions, shortcuts, etc.)
    if let Some(raw_payload) = form.get("payload") {
        let payload: Value = match serde_json::from_str(raw_payload) {
            Ok(v) => v,
            Err(_) => return SlackWebhookResult::Ignored,
        };
        return parse_interaction(&payload);
    }

    // Slash command
    let command = form.get("command").map(|s| s.trim().to_string());
    let command = match command {
        Some(c) if !c.is_empty() => c,
        _ => return SlackWebhookResult::Ignored,
    };

    let text = form.get("text").cloned().unwrap_or_default();
    let workspace_id = form.get("team_id").cloned().unwrap_or("unknown".into());
    let channel_id = form.get("channel_id").cloned().unwrap_or("unknown".into());
    let trigger_id = form.get("trigger_id").cloned().unwrap_or_default();
    let thread_ts = form.get("thread_ts").cloned().unwrap_or_default();
    let root_id = if !thread_ts.is_empty() {
        thread_ts
    } else if !trigger_id.is_empty() {
        trigger_id.clone()
    } else {
        format!("slash-{}", now_millis())
    };
    let message_id = if !trigger_id.is_empty() {
        trigger_id
    } else {
        root_id.clone()
    };
    let user_id = form.get("user_id").cloned().unwrap_or("unknown".into());
    let user_name = form.get("user_name").cloned();

    SlackWebhookResult::Event(Event {
        kind: EventKind::Command,
        platform: platform::slack(),
        workspace_id,
        channel_id,
        thread_id: root_id,
        message_id,
        user: UserInfo {
            id: user_id,
            name: user_name,
            email: None,
        },
        text,
        command: Some(command),
        emoji: None,
        raw_event_type: None,
        raw: form_to_value(&form),
    })
}

fn parse_interaction(payload: &Value) -> SlackWebhookResult {
    let interaction_type = payload.get("type").and_then(|v| v.as_str()).unwrap_or("");
    if !matches!(
        interaction_type,
        "block_actions" | "shortcut" | "message_action" | "view_submission"
    ) {
        return SlackWebhookResult::Ignored;
    }

    let user = payload.get("user").and_then(|v| v.as_object());
    let channel = payload.get("channel").and_then(|v| v.as_object());
    let team = payload.get("team").and_then(|v| v.as_object());
    let message = payload.get("message").and_then(|v| v.as_object());

    let text = message
        .and_then(|m| m.get("text"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let root_id = message
        .and_then(|m| m.get("thread_ts").or_else(|| m.get("ts")))
        .and_then(|v| v.as_str())
        .or_else(|| payload.get("trigger_id").and_then(|v| v.as_str()))
        .map(|s| s.to_string())
        .unwrap_or_else(|| format!("interaction-{}", now_millis()));

    let msg_id = message
        .and_then(|m| m.get("client_msg_id").or_else(|| m.get("ts")))
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| root_id.clone());

    SlackWebhookResult::Event(Event {
        kind: EventKind::Message,
        platform: platform::slack(),
        workspace_id: str_field_from_obj(team, "id", "unknown"),
        channel_id: str_field_from_obj(channel, "id", "unknown"),
        thread_id: root_id,
        message_id: msg_id,
        user: UserInfo {
            id: str_field_from_obj(user, "id", "unknown"),
            name: user
                .and_then(|u| u.get("username"))
                .and_then(|v| v.as_str())
                .map(String::from),
            email: None,
        },
        text,
        command: None,
        emoji: None,
        raw_event_type: None,
        raw: payload.clone(),
    })
}

fn parse_event_callback(payload: &Value) -> SlackWebhookResult {
    if payload.get("type").and_then(|v| v.as_str()) != Some("event_callback") {
        return SlackWebhookResult::Ignored;
    }

    let event = match payload.get("event").and_then(|v| v.as_object()) {
        Some(e) => e,
        None => return SlackWebhookResult::Ignored,
    };

    // Skip bot messages
    if event.get("bot_id").is_some()
        || event.get("subtype").and_then(|v| v.as_str()) == Some("bot_message")
    {
        return SlackWebhookResult::Ignored;
    }

    let event_type_raw = event.get("type").and_then(|v| v.as_str()).unwrap_or("");
    let raw_text = event.get("text").and_then(|v| v.as_str()).unwrap_or("");
    let text = strip_mentions(raw_text);

    let mention_re = Regex::new(r"<@[^>]+>").unwrap();
    let is_mention = event_type_raw == "app_mention" || mention_re.is_match(raw_text);

    let workspace_id = payload
        .get("team_id")
        .or_else(|| event.get(&"team".to_string()))
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let channel_id = event
        .get("channel")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let root_id = event
        .get("thread_ts")
        .or_else(|| event.get("ts"))
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_else(|| format!("event-{}", now_millis()));
    let message_id = event
        .get("client_msg_id")
        .or_else(|| event.get("ts"))
        .and_then(|v| v.as_str())
        .map(String::from)
        .unwrap_or_else(|| root_id.clone());
    let user_id = event
        .get("user")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();

    if event_type_raw == "reaction_added" {
        let emoji = event
            .get("reaction")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        return SlackWebhookResult::Event(Event {
            kind: EventKind::Reaction,
            platform: platform::slack(),
            workspace_id,
            channel_id,
            thread_id: root_id,
            message_id,
            user: UserInfo {
                id: user_id,
                name: None,
                email: None,
            },
            text,
            command: None,
            emoji: Some(emoji),
            raw_event_type: None,
            raw: payload.clone(),
        });
    }

    let kind = if is_mention {
        EventKind::Mention
    } else {
        EventKind::Message
    };

    SlackWebhookResult::Event(Event {
        kind,
        platform: platform::slack(),
        workspace_id,
        channel_id,
        thread_id: root_id,
        message_id,
        user: UserInfo {
            id: user_id,
            name: None,
            email: None,
        },
        text,
        command: None,
        emoji: None,
        raw_event_type: None,
        raw: payload.clone(),
    })
}

/// Strip Slack-style `<@UXXXX>` mentions from text.
pub fn strip_mentions(text: &str) -> String {
    let re = Regex::new(r"<@[^>]+>\s*").unwrap();
    re.replace_all(text, "").trim().to_string()
}

// -- helpers --

fn now_millis() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_millis()
}

fn parse_query_string(input: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    for pair in input.split('&') {
        if let Some((k, v)) = pair.split_once('=') {
            let key = urldecode(k);
            let val = urldecode(v);
            map.insert(key, val);
        }
    }
    map
}

fn urldecode(s: &str) -> String {
    let s = s.replace('+', " ");
    let mut result = String::with_capacity(s.len());
    let mut chars = s.chars();
    while let Some(c) = chars.next() {
        if c == '%' {
            let hex: String = chars.by_ref().take(2).collect();
            if let Ok(byte) = u8::from_str_radix(&hex, 16) {
                result.push(byte as char);
            } else {
                result.push('%');
                result.push_str(&hex);
            }
        } else {
            result.push(c);
        }
    }
    result
}

fn form_to_value(form: &HashMap<String, String>) -> Value {
    let map: serde_json::Map<String, Value> = form
        .iter()
        .map(|(k, v)| (k.clone(), Value::String(v.clone())))
        .collect();
    Value::Object(map)
}

fn str_field_from_obj(
    obj: Option<&serde_json::Map<String, Value>>,
    field: &str,
    default: &str,
) -> String {
    obj.and_then(|o| o.get(field))
        .and_then(|v| v.as_str())
        .unwrap_or(default)
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_strip_mentions() {
        assert_eq!(strip_mentions("<@U123> hello"), "hello");
        assert_eq!(strip_mentions("no mentions"), "no mentions");
        assert_eq!(strip_mentions("<@U1> <@U2> hi"), "hi");
    }

    #[test]
    fn test_verify_signature_valid() {
        let secret = "test-secret";
        let body = b"hello";
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            .to_string();

        let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).unwrap();
        mac.update(format!("v0:{ts}:hello").as_bytes());
        let sig = format!("v0={}", hex::encode(mac.finalize().into_bytes()));

        assert!(verify_signature(secret, &ts, &sig, body));
    }

    #[test]
    fn test_verify_signature_invalid() {
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            .to_string();
        assert!(!verify_signature("secret", &ts, "v0=bad", b"body"));
    }

    #[test]
    fn test_url_verification() {
        let body = br#"{"type":"url_verification","challenge":"abc123"}"#;
        let headers = HashMap::new();
        match parse_webhook(body, "application/json", &headers) {
            SlackWebhookResult::Challenge(c) => assert_eq!(c, "abc123"),
            _ => panic!("expected Challenge"),
        }
    }

    #[test]
    fn test_parse_mention_event() {
        let body = serde_json::to_vec(&serde_json::json!({
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "app_mention",
                "text": "<@UBOT> hello",
                "channel": "C1",
                "ts": "1234.5678",
                "user": "U1"
            }
        }))
        .unwrap();
        let headers = HashMap::new();
        match parse_webhook(&body, "application/json", &headers) {
            SlackWebhookResult::Event(e) => {
                assert_eq!(e.kind, EventKind::Mention);
                assert_eq!(e.text, "hello");
                assert_eq!(e.workspace_id, "T1");
                assert_eq!(e.channel_id, "C1");
                assert_eq!(e.user.id, "U1");
            }
            _ => panic!("expected Event"),
        }
    }

    #[test]
    fn test_parse_slash_command() {
        let body = b"command=%2Fecho&text=hello+world&team_id=T1&channel_id=C1&user_id=U1&user_name=alice&trigger_id=trig1";
        let headers = HashMap::new();
        match parse_webhook(body, "application/x-www-form-urlencoded", &headers) {
            SlackWebhookResult::Event(e) => {
                assert_eq!(e.kind, EventKind::Command);
                assert_eq!(e.command, Some("/echo".into()));
                assert_eq!(e.text, "hello world");
                assert_eq!(e.user.id, "U1");
                assert_eq!(e.user.name, Some("alice".into()));
            }
            _ => panic!("expected Event"),
        }
    }

    #[test]
    fn test_skip_bot_messages() {
        let body = serde_json::to_vec(&serde_json::json!({
            "type": "event_callback",
            "event": {
                "type": "message",
                "bot_id": "B1",
                "text": "bot says hi",
                "channel": "C1",
                "ts": "1234.5678"
            }
        }))
        .unwrap();
        let headers = HashMap::new();
        assert!(matches!(
            parse_webhook(&body, "application/json", &headers),
            SlackWebhookResult::Ignored
        ));
    }

    #[test]
    fn test_parse_reaction() {
        let body = serde_json::to_vec(&serde_json::json!({
            "type": "event_callback",
            "team_id": "T1",
            "event": {
                "type": "reaction_added",
                "reaction": "thumbsup",
                "channel": "C1",
                "ts": "1234.5678",
                "user": "U1"
            }
        }))
        .unwrap();
        let headers = HashMap::new();
        match parse_webhook(&body, "application/json", &headers) {
            SlackWebhookResult::Event(e) => {
                assert_eq!(e.kind, EventKind::Reaction);
                assert_eq!(e.emoji, Some("thumbsup".into()));
            }
            _ => panic!("expected Event"),
        }
    }
}
