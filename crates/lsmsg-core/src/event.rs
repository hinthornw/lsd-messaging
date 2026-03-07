use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

use crate::platform::{Platform, PlatformCapabilities};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    Message,
    Mention,
    Command,
    Reaction,
    Raw,
}

impl EventKind {
    pub fn as_str(&self) -> &str {
        match self {
            Self::Message => "message",
            Self::Mention => "mention",
            Self::Command => "command",
            Self::Reaction => "reaction",
            Self::Raw => "raw",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct UserInfo {
    pub id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub email: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Event {
    pub kind: EventKind,
    pub platform: PlatformCapabilities,
    pub workspace_id: String,
    pub channel_id: String,
    pub thread_id: String,
    pub message_id: String,
    pub user: UserInfo,
    pub text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub emoji: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw_event_type: Option<String>,
    #[serde(default)]
    pub raw: Value,
}

impl Event {
    pub fn internal_thread_id(&self) -> String {
        deterministic_thread_id(
            &self.platform.name,
            &self.workspace_id,
            &self.channel_id,
            &self.thread_id,
        )
    }
}

/// Produces a stable UUID v5 thread identifier from platform coordinates.
/// Uses the same namespace UUID as the Python implementation.
pub fn deterministic_thread_id(
    platform: &Platform,
    workspace_id: &str,
    channel_id: &str,
    thread_id: &str,
) -> String {
    let namespace = Uuid::parse_str("6ba7b810-9dad-11d1-80b4-00c04fd430c8").unwrap();
    let key = format!(
        "{}:{}:{}:{}",
        platform.as_str(),
        workspace_id,
        channel_id,
        thread_id
    );
    Uuid::new_v5(&namespace, key.as_bytes()).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deterministic_thread_id_is_stable() {
        let id1 = deterministic_thread_id(&Platform::Slack, "T1", "C1", "t1");
        let id2 = deterministic_thread_id(&Platform::Slack, "T1", "C1", "t1");
        assert_eq!(id1, id2);
    }

    #[test]
    fn different_inputs_different_ids() {
        let id1 = deterministic_thread_id(&Platform::Slack, "T1", "C1", "t1");
        let id2 = deterministic_thread_id(&Platform::Teams, "T1", "C1", "t1");
        assert_ne!(id1, id2);
    }
}
