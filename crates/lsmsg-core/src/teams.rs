use regex::Regex;
use serde_json::Value;

use crate::event::{Event, EventKind, UserInfo};
use crate::platform;

/// Parse a Teams Bot Framework activity payload into an Event.
/// Returns `None` for activities we should ignore.
pub fn parse_webhook(payload: &Value) -> Option<Event> {
    let activity_type = payload.get("type").and_then(|v| v.as_str()).unwrap_or("");

    if activity_type == "messageReaction" {
        return parse_reaction(payload);
    }

    if activity_type != "message" {
        return None;
    }

    let from_user = payload.get("from").and_then(|v| v.as_object());
    let conversation = payload.get("conversation").and_then(|v| v.as_object());
    let channel_data = payload.get("channelData").and_then(|v| v.as_object());
    let tenant = channel_data
        .and_then(|cd| cd.get("tenant"))
        .and_then(|v| v.as_object());
    let team = channel_data
        .and_then(|cd| cd.get("team"))
        .and_then(|v| v.as_object());

    let raw_text = payload.get("text").and_then(|v| v.as_str()).unwrap_or("");
    let text = strip_mentions(raw_text);

    let entities = payload.get("entities").and_then(|v| v.as_array());
    let has_mention = entities
        .map(|arr| {
            arr.iter().any(|e| {
                e.as_object()
                    .and_then(|o| o.get("type"))
                    .and_then(|v| v.as_str())
                    == Some("mention")
            })
        })
        .unwrap_or(false);
    let is_mention = has_mention || text != raw_text;

    let conversation_id = obj_str(conversation, "id").unwrap_or_else(|| "unknown".into());
    let root_id = payload
        .get("replyToId")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .unwrap_or_else(|| {
            if !conversation_id.is_empty() && conversation_id != "unknown" {
                conversation_id.clone()
            } else {
                payload
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown")
                    .to_string()
            }
        });

    let message_id = payload
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or(&root_id)
        .to_string();

    let workspace_id = obj_str(tenant, "id")
        .or_else(|| obj_str(conversation, "tenantId"))
        .unwrap_or_else(|| "unknown".into());

    let channel_id = obj_str(team, "id").unwrap_or_else(|| conversation_id.clone());

    let user_id = obj_str(from_user, "id").unwrap_or_else(|| "unknown".into());
    let user_name = from_user
        .and_then(|u| u.get("name"))
        .and_then(|v| v.as_str())
        .map(String::from);

    let kind = if is_mention {
        EventKind::Mention
    } else {
        EventKind::Message
    };

    Some(Event {
        kind,
        platform: platform::teams(),
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
        command: None,
        emoji: None,
        raw_event_type: None,
        raw: payload.clone(),
    })
}

fn parse_reaction(payload: &Value) -> Option<Event> {
    let from_user = payload.get("from").and_then(|v| v.as_object());
    let conversation = payload.get("conversation").and_then(|v| v.as_object());
    let channel_data = payload.get("channelData").and_then(|v| v.as_object());
    let tenant = channel_data
        .and_then(|cd| cd.get("tenant"))
        .and_then(|v| v.as_object());
    let team = channel_data
        .and_then(|cd| cd.get("team"))
        .and_then(|v| v.as_object());

    let reactions_added = payload.get("reactionsAdded").and_then(|v| v.as_array())?;
    if reactions_added.is_empty() {
        return None;
    }
    let emoji = reactions_added[0]
        .get("type")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let conversation_id = obj_str(conversation, "id").unwrap_or_else(|| "unknown".into());
    let root_id = payload
        .get("replyToId")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(String::from)
        .unwrap_or_else(|| conversation_id.clone());

    Some(Event {
        kind: EventKind::Reaction,
        platform: platform::teams(),
        workspace_id: obj_str(tenant, "id").unwrap_or_else(|| "unknown".into()),
        channel_id: obj_str(team, "id").unwrap_or_else(|| conversation_id.clone()),
        thread_id: root_id.clone(),
        message_id: payload
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or(&root_id)
            .to_string(),
        user: UserInfo {
            id: obj_str(from_user, "id").unwrap_or_else(|| "unknown".into()),
            name: from_user
                .and_then(|u| u.get("name"))
                .and_then(|v| v.as_str())
                .map(String::from),
            email: None,
        },
        text: String::new(),
        command: None,
        emoji: Some(emoji),
        raw_event_type: None,
        raw: payload.clone(),
    })
}

/// Strip Teams-style `<at>Name</at>` mentions from text.
pub fn strip_mentions(text: &str) -> String {
    let re = Regex::new(r"(?i)<at>.*?</at>").unwrap();
    let without = re.replace_all(text, "");
    let ws = Regex::new(r"\s+").unwrap();
    ws.replace_all(without.trim(), " ").to_string()
}

fn obj_str(obj: Option<&serde_json::Map<String, Value>>, field: &str) -> Option<String> {
    obj.and_then(|o| o.get(field))
        .and_then(|v| v.as_str())
        .map(String::from)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_strip_mentions() {
        assert_eq!(strip_mentions("<at>Bot</at> hello"), "hello");
        assert_eq!(strip_mentions("no mentions"), "no mentions");
        assert_eq!(
            strip_mentions("<at>Bot</at>  multiple  spaces"),
            "multiple spaces"
        );
    }

    #[test]
    fn test_parse_message() {
        let payload = json!({
            "type": "message",
            "text": "hello",
            "from": {"id": "U1", "name": "Alice"},
            "conversation": {"id": "conv-1", "tenantId": "tenant-1"},
            "channelData": {
                "tenant": {"id": "tenant-1"},
                "team": {"id": "team-1"}
            },
            "id": "msg-1"
        });

        let event = parse_webhook(&payload).unwrap();
        assert_eq!(event.kind, EventKind::Message);
        assert_eq!(event.text, "hello");
        assert_eq!(event.user.id, "U1");
        assert_eq!(event.user.name, Some("Alice".into()));
    }

    #[test]
    fn test_parse_mention() {
        let payload = json!({
            "type": "message",
            "text": "<at>Bot</at> help me",
            "from": {"id": "U1"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "id": "msg-1",
            "entities": [{"type": "mention", "mentioned": {"id": "bot-1"}}]
        });

        let event = parse_webhook(&payload).unwrap();
        assert_eq!(event.kind, EventKind::Mention);
        assert_eq!(event.text, "help me");
    }

    #[test]
    fn test_parse_reaction() {
        let payload = json!({
            "type": "messageReaction",
            "reactionsAdded": [{"type": "like"}],
            "from": {"id": "U1", "name": "Bob"},
            "conversation": {"id": "conv-1"},
            "channelData": {"tenant": {"id": "t1"}, "team": {"id": "team-1"}},
            "replyToId": "msg-orig",
            "id": "reaction-1"
        });

        let event = parse_webhook(&payload).unwrap();
        assert_eq!(event.kind, EventKind::Reaction);
        assert_eq!(event.emoji, Some("like".into()));
    }

    #[test]
    fn test_ignore_unknown_type() {
        let payload = json!({"type": "typing"});
        assert!(parse_webhook(&payload).is_none());
    }
}
