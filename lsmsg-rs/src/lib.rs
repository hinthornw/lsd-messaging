#![allow(
    clippy::derivable_impls,
    clippy::too_many_arguments,
    clippy::collapsible_if
)]

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use dashmap::{DashMap, DashSet};
use parking_lot::RwLock;
use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyDict};
use pythonize::{depythonize, pythonize};
use regex::Regex;
use reqwest::Method;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use serde_json::json;
use thiserror::Error;
use time::OffsetDateTime;
use time::format_description::well_known::Rfc3339;
use urlencoding::encode;
use uuid::Uuid;

const THREAD_STATE_TTL: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const CHANNEL_STATE_TTL: Duration = Duration::from_secs(30 * 24 * 60 * 60);
const LANGGRAPH_THREAD_NAMESPACE_SEED: &str = "lsmsg:langgraph:thread:v1";

#[derive(Debug, Error)]
enum ChatError {
    #[error("adapter '{0}' not found")]
    AdapterNotFound(String),
    #[error("adapter '{0}' already registered")]
    AdapterAlreadyExists(String),
    #[error("could not acquire lock for thread '{0}'")]
    LockBusy(String),
    #[error("invalid regex pattern: {0}")]
    InvalidPattern(String),
    #[error("message '{0}' not found")]
    MessageNotFound(String),
    #[error("message '{0}' does not belong to thread '{1}'")]
    MessageThreadMismatch(String, String),
    #[error("state serialization error: {0}")]
    Serialization(String),
    #[error("invalid channel id '{0}'")]
    InvalidChannelId(String),
    #[error("invalid thread id '{0}'")]
    InvalidThreadId(String),
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("http error: {0}")]
    Http(String),
    #[error("upstream api error: {0}")]
    Api(String),
}

impl From<ChatError> for PyErr {
    fn from(value: ChatError) -> Self {
        match value {
            ChatError::InvalidPattern(msg) => PyValueError::new_err(msg),
            ChatError::InvalidChannelId(msg) => PyValueError::new_err(msg),
            ChatError::InvalidThreadId(msg) => PyValueError::new_err(msg),
            ChatError::InvalidArgument(msg) => PyValueError::new_err(msg),
            _ => PyRuntimeError::new_err(value.to_string()),
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
struct AuthorData {
    user_id: String,
    user_name: String,
    full_name: String,
    is_bot: bool,
    is_me: bool,
}

impl Default for AuthorData {
    fn default() -> Self {
        Self {
            user_id: String::new(),
            user_name: String::new(),
            full_name: String::new(),
            is_bot: false,
            is_me: false,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
struct MessageMetadataData {
    date_sent_ms: i64,
    edited: bool,
    edited_at_ms: Option<i64>,
}

impl Default for MessageMetadataData {
    fn default() -> Self {
        Self {
            date_sent_ms: now_millis(),
            edited: false,
            edited_at_ms: None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
struct AttachmentData {
    kind: String,
    url: Option<String>,
    name: Option<String>,
    mime_type: Option<String>,
    size: Option<u64>,
}

impl Default for AttachmentData {
    fn default() -> Self {
        Self {
            kind: "file".to_string(),
            url: None,
            name: None,
            mime_type: None,
            size: None,
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default)]
struct MessageData {
    id: String,
    thread_id: String,
    text: String,
    author: AuthorData,
    metadata: MessageMetadataData,
    attachments: Vec<AttachmentData>,
    is_mention: Option<bool>,
    formatted: Option<Value>,
    raw: Option<Value>,
}

impl Default for MessageData {
    fn default() -> Self {
        Self {
            id: String::new(),
            thread_id: String::new(),
            text: String::new(),
            author: AuthorData::default(),
            metadata: MessageMetadataData::default(),
            attachments: Vec::new(),
            is_mention: None,
            formatted: None,
            raw: None,
        }
    }
}

#[derive(Clone, Debug)]
struct RawSentMessage {
    id: String,
    thread_id: String,
}

#[derive(Clone, Debug)]
enum PostableMessage {
    Text(String),
    Markdown(String),
    Raw(String),
    Json(Value),
}

impl PostableMessage {
    fn plain_text(&self) -> String {
        match self {
            PostableMessage::Text(text) => text.clone(),
            PostableMessage::Markdown(markdown) => markdown.clone(),
            PostableMessage::Raw(raw) => raw.clone(),
            PostableMessage::Json(value) => value.to_string(),
        }
    }
}

#[derive(Clone)]
struct CacheEntry {
    value: Value,
    expires_at: Option<Instant>,
}

#[derive(Clone)]
struct LockEntry {
    token: String,
    expires_at: Instant,
}

#[derive(Clone)]
struct LockToken {
    thread_id: String,
    token: String,
}

#[derive(Clone, Default)]
struct MemoryState {
    subscriptions: Arc<DashSet<String>>,
    cache: Arc<DashMap<String, CacheEntry>>,
    locks: Arc<DashMap<String, LockEntry>>,
}

impl MemoryState {
    fn subscribe(&self, thread_id: &str) {
        self.subscriptions.insert(thread_id.to_string());
    }

    fn unsubscribe(&self, thread_id: &str) {
        self.subscriptions.remove(thread_id);
    }

    fn is_subscribed(&self, thread_id: &str) -> bool {
        self.subscriptions.contains(thread_id)
    }

    fn acquire_lock(&self, thread_id: &str, ttl: Duration) -> Option<LockToken> {
        self.cleanup_expired_lock(thread_id);
        if let Some(existing) = self.locks.get(thread_id) {
            if existing.expires_at > Instant::now() {
                return None;
            }
        }

        let token = Uuid::new_v4().to_string();
        let entry = LockEntry {
            token: token.clone(),
            expires_at: Instant::now() + ttl,
        };
        self.locks.insert(thread_id.to_string(), entry);

        Some(LockToken {
            thread_id: thread_id.to_string(),
            token,
        })
    }

    fn release_lock(&self, lock: &LockToken) {
        let should_remove = self
            .locks
            .get(&lock.thread_id)
            .map(|existing| existing.token == lock.token)
            .unwrap_or(false);
        if should_remove {
            self.locks.remove(&lock.thread_id);
        }
    }

    fn get(&self, key: &str) -> Option<Value> {
        if let Some(entry) = self.cache.get(key) {
            let expires_at = entry.expires_at;
            if let Some(expires_at) = expires_at {
                if expires_at <= Instant::now() {
                    drop(entry);
                    self.cache.remove(key);
                    return None;
                }
            }
            return Some(entry.value.clone());
        }
        None
    }

    fn set(&self, key: &str, value: Value, ttl: Option<Duration>) {
        let expires_at = ttl.map(|duration| Instant::now() + duration);
        self.cache
            .insert(key.to_string(), CacheEntry { value, expires_at });
    }

    fn cleanup_expired_lock(&self, thread_id: &str) {
        let is_expired = self
            .locks
            .get(thread_id)
            .map(|existing| existing.expires_at <= Instant::now())
            .unwrap_or(false);
        if is_expired {
            self.locks.remove(thread_id);
        }
    }
}

trait Adapter: Send + Sync {
    fn name(&self) -> &str;
    fn user_name(&self) -> &str;
    fn bot_user_id(&self) -> Option<&str> {
        None
    }
    fn is_dm(&self, _thread_id: &str) -> bool {
        false
    }
    fn channel_id_from_thread_id(&self, thread_id: &str) -> String {
        derive_channel_id(thread_id)
    }
    fn mention_user(&self, user_id: &str) -> String {
        format!("<@{}>", user_id)
    }

    fn post_message(
        &self,
        thread_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError>;
    fn post_channel_message(
        &self,
        channel_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        self.post_message(channel_id, message)
    }
    fn edit_message(
        &self,
        thread_id: &str,
        message_id: &str,
        message: &PostableMessage,
    ) -> Result<(), ChatError>;
    fn delete_message(&self, thread_id: &str, message_id: &str) -> Result<(), ChatError>;
    fn add_reaction(
        &self,
        _thread_id: &str,
        _message_id: &str,
        _emoji: &str,
    ) -> Result<(), ChatError> {
        Ok(())
    }
    fn remove_reaction(
        &self,
        _thread_id: &str,
        _message_id: &str,
        _emoji: &str,
    ) -> Result<(), ChatError> {
        Ok(())
    }
    fn fetch_messages(&self, thread_id: &str, limit: usize) -> Result<Vec<MessageData>, ChatError>;
    fn fetch_message_by_id(
        &self,
        thread_id: &str,
        message_id: &str,
    ) -> Result<Option<MessageData>, ChatError> {
        let messages = self.fetch_messages(thread_id, 200)?;
        Ok(messages
            .into_iter()
            .find(|message| message.id == message_id))
    }
}

#[derive(Default)]
struct InMemoryAdapter {
    name: String,
    user_name: String,
    bot_user_id: Option<String>,
    sequence: AtomicU64,
    messages_by_id: DashMap<String, MessageData>,
    thread_messages: DashMap<String, Vec<String>>,
    reactions: DashMap<(String, String), HashSet<String>>,
}

impl InMemoryAdapter {
    fn new(name: String, user_name: String, bot_user_id: Option<String>) -> Self {
        Self {
            name,
            user_name,
            bot_user_id,
            sequence: AtomicU64::new(1),
            messages_by_id: DashMap::new(),
            thread_messages: DashMap::new(),
            reactions: DashMap::new(),
        }
    }

    fn self_author(&self) -> AuthorData {
        AuthorData {
            user_id: "self".to_string(),
            user_name: self.user_name.clone(),
            full_name: self.user_name.clone(),
            is_bot: true,
            is_me: true,
        }
    }

    fn next_message_id(&self) -> String {
        let id = self.sequence.fetch_add(1, Ordering::Relaxed);
        format!("msg_{id}")
    }
}

impl Adapter for InMemoryAdapter {
    fn name(&self) -> &str {
        &self.name
    }

    fn user_name(&self) -> &str {
        &self.user_name
    }

    fn bot_user_id(&self) -> Option<&str> {
        self.bot_user_id.as_deref()
    }

    fn post_message(
        &self,
        thread_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        let id = self.next_message_id();
        let text = message.plain_text();

        let record = MessageData {
            id: id.clone(),
            thread_id: thread_id.to_string(),
            text,
            author: self.self_author(),
            metadata: MessageMetadataData {
                date_sent_ms: now_millis(),
                edited: false,
                edited_at_ms: None,
            },
            attachments: Vec::new(),
            is_mention: None,
            formatted: None,
            raw: None,
        };

        self.messages_by_id.insert(id.clone(), record);
        self.thread_messages
            .entry(thread_id.to_string())
            .or_default()
            .push(id.clone());

        Ok(RawSentMessage {
            id,
            thread_id: thread_id.to_string(),
        })
    }

    fn edit_message(
        &self,
        thread_id: &str,
        message_id: &str,
        message: &PostableMessage,
    ) -> Result<(), ChatError> {
        let mut entry = self
            .messages_by_id
            .get_mut(message_id)
            .ok_or_else(|| ChatError::MessageNotFound(message_id.to_string()))?;

        if entry.thread_id != thread_id {
            return Err(ChatError::MessageThreadMismatch(
                message_id.to_string(),
                thread_id.to_string(),
            ));
        }

        entry.text = message.plain_text();
        entry.metadata.edited = true;
        entry.metadata.edited_at_ms = Some(now_millis());
        Ok(())
    }

    fn delete_message(&self, thread_id: &str, message_id: &str) -> Result<(), ChatError> {
        let (message_id_owned, message) = self
            .messages_by_id
            .remove(message_id)
            .ok_or_else(|| ChatError::MessageNotFound(message_id.to_string()))?;

        if message.thread_id != thread_id {
            self.messages_by_id.insert(message_id_owned, message);
            return Err(ChatError::MessageThreadMismatch(
                message_id.to_string(),
                thread_id.to_string(),
            ));
        }

        if let Some(mut thread) = self.thread_messages.get_mut(thread_id) {
            thread.retain(|id| id != message_id);
        }
        self.reactions
            .remove(&(thread_id.to_string(), message_id.to_string()));
        Ok(())
    }

    fn add_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        self.reactions
            .entry((thread_id.to_string(), message_id.to_string()))
            .or_default()
            .insert(emoji.to_string());
        Ok(())
    }

    fn remove_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        if let Some(mut reactions) = self
            .reactions
            .get_mut(&(thread_id.to_string(), message_id.to_string()))
        {
            reactions.remove(emoji);
        }
        Ok(())
    }

    fn fetch_messages(&self, thread_id: &str, limit: usize) -> Result<Vec<MessageData>, ChatError> {
        let ids = if let Some(ids) = self.thread_messages.get(thread_id) {
            ids.clone()
        } else {
            return Ok(Vec::new());
        };

        let start = ids.len().saturating_sub(limit);
        let mut out = Vec::with_capacity(ids.len().saturating_sub(start));
        for id in &ids[start..] {
            if let Some(message) = self.messages_by_id.get(id) {
                out.push(message.clone());
            }
        }
        Ok(out)
    }

    fn fetch_message_by_id(
        &self,
        thread_id: &str,
        message_id: &str,
    ) -> Result<Option<MessageData>, ChatError> {
        let message = self
            .messages_by_id
            .get(message_id)
            .map(|entry| entry.clone());
        if let Some(message) = message {
            if message.thread_id == thread_id {
                return Ok(Some(message));
            }
            return Ok(None);
        }
        Ok(None)
    }
}

struct SlackAdapter {
    user_name: String,
    bot_user_id: Option<String>,
    bot_token: String,
    api_base_url: String,
    client: Client,
}

impl SlackAdapter {
    fn new(
        bot_token: String,
        user_name: String,
        bot_user_id: Option<String>,
        api_base_url: Option<String>,
    ) -> Result<Self, ChatError> {
        let api_base_url = api_base_url
            .unwrap_or_else(|| "https://slack.com/api".to_string())
            .trim_end_matches('/')
            .to_string();

        let client = Client::builder()
            .build()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        Ok(Self {
            user_name,
            bot_user_id,
            bot_token,
            api_base_url,
            client,
        })
    }

    fn api_post(&self, endpoint: &str, body: Value) -> Result<Value, ChatError> {
        let url = format!("{}/{}", self.api_base_url, endpoint);
        let response = self
            .client
            .post(url)
            .bearer_auth(&self.bot_token)
            .json(&body)
            .send()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        let status = response.status();
        let payload: Value = response
            .json()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        if !status.is_success() {
            return Err(ChatError::Http(format!(
                "slack {} failed with status {}: {}",
                endpoint, status, payload
            )));
        }

        if payload.get("ok").and_then(Value::as_bool) == Some(false) {
            let detail = payload
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown slack error");
            return Err(ChatError::Api(format!("slack {}: {}", endpoint, detail)));
        }

        Ok(payload)
    }

    fn api_get(&self, endpoint: &str, query: &[(&str, String)]) -> Result<Value, ChatError> {
        let url = format!("{}/{}", self.api_base_url, endpoint);
        let response = self
            .client
            .get(url)
            .bearer_auth(&self.bot_token)
            .query(query)
            .send()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        let status = response.status();
        let payload: Value = response
            .json()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        if !status.is_success() {
            return Err(ChatError::Http(format!(
                "slack {} failed with status {}: {}",
                endpoint, status, payload
            )));
        }

        if payload.get("ok").and_then(Value::as_bool) == Some(false) {
            let detail = payload
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown slack error");
            return Err(ChatError::Api(format!("slack {}: {}", endpoint, detail)));
        }

        Ok(payload)
    }

    fn parse_message(
        &self,
        channel_id: &str,
        value: &Value,
        fallback_thread_ts: Option<&str>,
    ) -> MessageData {
        let message_id = value
            .get("ts")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| format!("slack_{}", Uuid::new_v4()));

        let thread_ts = value
            .get("thread_ts")
            .and_then(Value::as_str)
            .or(fallback_thread_ts)
            .unwrap_or(message_id.as_str())
            .to_string();

        let text = value
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();

        let user_id = value
            .get("user")
            .and_then(Value::as_str)
            .or_else(|| value.get("bot_id").and_then(Value::as_str))
            .unwrap_or("unknown")
            .to_string();

        let user_name = value
            .get("username")
            .and_then(Value::as_str)
            .unwrap_or(user_id.as_str())
            .to_string();

        let is_bot = value.get("bot_id").is_some()
            || value
                .get("subtype")
                .and_then(Value::as_str)
                .map(|subtype| subtype == "bot_message")
                .unwrap_or(false);

        let date_sent_ms = value
            .get("ts")
            .and_then(Value::as_str)
            .and_then(parse_slack_ts_millis)
            .unwrap_or_else(now_millis);

        let edited_at_ms = value
            .get("edited")
            .and_then(Value::as_object)
            .and_then(|edited| edited.get("ts"))
            .and_then(Value::as_str)
            .and_then(parse_slack_ts_millis);

        let is_me = self
            .bot_user_id
            .as_deref()
            .map(|bot_user_id| bot_user_id == user_id)
            .unwrap_or(false);

        MessageData {
            id: message_id,
            thread_id: format!("slack:{channel_id}:{thread_ts}"),
            text,
            author: AuthorData {
                user_id: user_id.clone(),
                user_name: user_name.clone(),
                full_name: user_name,
                is_bot,
                is_me,
            },
            metadata: MessageMetadataData {
                date_sent_ms,
                edited: edited_at_ms.is_some(),
                edited_at_ms,
            },
            attachments: Vec::new(),
            is_mention: None,
            formatted: None,
            raw: Some(value.clone()),
        }
    }
}

impl Adapter for SlackAdapter {
    fn name(&self) -> &str {
        "slack"
    }

    fn user_name(&self) -> &str {
        &self.user_name
    }

    fn bot_user_id(&self) -> Option<&str> {
        self.bot_user_id.as_deref()
    }

    fn is_dm(&self, thread_id: &str) -> bool {
        parse_adapter_thread_id("slack", thread_id)
            .map(|(channel, _)| channel.starts_with('D'))
            .unwrap_or(false)
    }

    fn post_message(
        &self,
        thread_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        let (channel_id, thread_ts) = parse_adapter_thread_id("slack", thread_id)?;
        let mut body = json!({
            "channel": channel_id,
            "text": message.plain_text(),
            "mrkdwn": true
        });

        if let Some(thread_ts) = thread_ts.clone() {
            body["thread_ts"] = Value::String(thread_ts);
        }

        let response = self.api_post("chat.postMessage", body)?;
        let message_id = response
            .get("ts")
            .and_then(Value::as_str)
            .ok_or_else(|| ChatError::Api("slack chat.postMessage missing ts".to_string()))?
            .to_string();

        let resolved_channel = response
            .get("channel")
            .and_then(Value::as_str)
            .unwrap_or(channel_id.as_str())
            .to_string();
        let root_thread_ts = thread_ts.unwrap_or_else(|| message_id.clone());

        Ok(RawSentMessage {
            id: message_id,
            thread_id: format!("slack:{resolved_channel}:{root_thread_ts}"),
        })
    }

    fn post_channel_message(
        &self,
        channel_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        let channel = parse_adapter_channel_id("slack", channel_id)?;
        let response = self.api_post(
            "chat.postMessage",
            json!({
                "channel": channel,
                "text": message.plain_text(),
                "mrkdwn": true
            }),
        )?;

        let message_id = response
            .get("ts")
            .and_then(Value::as_str)
            .ok_or_else(|| ChatError::Api("slack chat.postMessage missing ts".to_string()))?
            .to_string();

        Ok(RawSentMessage {
            id: message_id.clone(),
            thread_id: format!("slack:{channel}:{message_id}"),
        })
    }

    fn edit_message(
        &self,
        thread_id: &str,
        message_id: &str,
        message: &PostableMessage,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("slack", thread_id)?;
        self.api_post(
            "chat.update",
            json!({
                "channel": channel_id,
                "ts": message_id,
                "text": message.plain_text(),
                "mrkdwn": true
            }),
        )?;
        Ok(())
    }

    fn delete_message(&self, thread_id: &str, message_id: &str) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("slack", thread_id)?;
        self.api_post(
            "chat.delete",
            json!({
                "channel": channel_id,
                "ts": message_id
            }),
        )?;
        Ok(())
    }

    fn add_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("slack", thread_id)?;
        self.api_post(
            "reactions.add",
            json!({
                "channel": channel_id,
                "timestamp": message_id,
                "name": slack_reaction_name(emoji)
            }),
        )?;
        Ok(())
    }

    fn remove_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("slack", thread_id)?;
        self.api_post(
            "reactions.remove",
            json!({
                "channel": channel_id,
                "timestamp": message_id,
                "name": slack_reaction_name(emoji)
            }),
        )?;
        Ok(())
    }

    fn fetch_messages(&self, thread_id: &str, limit: usize) -> Result<Vec<MessageData>, ChatError> {
        let (channel_id, thread_ts) = parse_adapter_thread_id("slack", thread_id)?;
        let bounded_limit = limit.clamp(1, 200);

        let response = if let Some(thread_ts) = thread_ts.clone() {
            self.api_get(
                "conversations.replies",
                &[
                    ("channel", channel_id.clone()),
                    ("ts", thread_ts),
                    ("limit", bounded_limit.to_string()),
                ],
            )?
        } else {
            self.api_get(
                "conversations.history",
                &[
                    ("channel", channel_id.clone()),
                    ("limit", bounded_limit.to_string()),
                ],
            )?
        };

        let mut messages = response
            .get("messages")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .map(|value| self.parse_message(&channel_id, &value, thread_ts.as_deref()))
            .collect::<Vec<_>>();

        messages.sort_by_key(|message| message.metadata.date_sent_ms);
        Ok(messages)
    }

    fn fetch_message_by_id(
        &self,
        thread_id: &str,
        message_id: &str,
    ) -> Result<Option<MessageData>, ChatError> {
        let (channel_id, thread_ts) = parse_adapter_thread_id("slack", thread_id)?;
        let response = if let Some(thread_ts) = thread_ts.clone() {
            self.api_get(
                "conversations.replies",
                &[
                    ("channel", channel_id.clone()),
                    ("ts", thread_ts),
                    ("latest", message_id.to_string()),
                    ("inclusive", "true".to_string()),
                    ("limit", "1".to_string()),
                ],
            )?
        } else {
            self.api_get(
                "conversations.history",
                &[
                    ("channel", channel_id.clone()),
                    ("latest", message_id.to_string()),
                    ("inclusive", "true".to_string()),
                    ("limit", "1".to_string()),
                ],
            )?
        };

        let messages = response
            .get("messages")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        for value in messages {
            let parsed = self.parse_message(&channel_id, &value, thread_ts.as_deref());
            if parsed.id == message_id {
                return Ok(Some(parsed));
            }
        }
        Ok(None)
    }
}

struct DiscordAdapter {
    user_name: String,
    bot_user_id: Option<String>,
    bot_token: String,
    api_base_url: String,
    client: Client,
}

impl DiscordAdapter {
    fn new(
        bot_token: String,
        user_name: String,
        bot_user_id: Option<String>,
        api_base_url: Option<String>,
    ) -> Result<Self, ChatError> {
        let api_base_url = api_base_url
            .unwrap_or_else(|| "https://discord.com/api/v10".to_string())
            .trim_end_matches('/')
            .to_string();

        let client = Client::builder()
            .build()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        Ok(Self {
            user_name,
            bot_user_id,
            bot_token,
            api_base_url,
            client,
        })
    }

    fn request(&self, method: Method, path: &str, body: Option<Value>) -> Result<Value, ChatError> {
        let url = format!("{}/{}", self.api_base_url, path.trim_start_matches('/'));
        let mut request = self
            .client
            .request(method, url)
            .header("Authorization", format!("Bot {}", self.bot_token))
            .header("User-Agent", "lsmsg-rs/0.1");

        if let Some(body) = body {
            request = request.json(&body);
        }

        let response = request
            .send()
            .map_err(|err| ChatError::Http(err.to_string()))?;
        let status = response.status();
        if status == reqwest::StatusCode::NO_CONTENT {
            return Ok(Value::Null);
        }

        let payload: Value = response
            .json()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        if !status.is_success() {
            return Err(ChatError::Http(format!(
                "discord {} failed with status {}: {}",
                path, status, payload
            )));
        }

        Ok(payload)
    }

    fn parse_message(&self, channel_id: &str, root_id: Option<&str>, value: &Value) -> MessageData {
        let id = value
            .get("id")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| format!("discord_{}", Uuid::new_v4()));

        let text = value
            .get("content")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();

        let author = value.get("author").and_then(Value::as_object);
        let user_id = author
            .and_then(|author| author.get("id"))
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        let user_name = author
            .and_then(|author| author.get("username"))
            .and_then(Value::as_str)
            .unwrap_or(user_id.as_str())
            .to_string();
        let is_bot = author
            .and_then(|author| author.get("bot"))
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let is_me = self
            .bot_user_id
            .as_deref()
            .map(|bot_user_id| bot_user_id == user_id)
            .unwrap_or(false);

        let date_sent_ms = value
            .get("timestamp")
            .and_then(Value::as_str)
            .and_then(parse_rfc3339_millis)
            .unwrap_or_else(now_millis);

        let edited_at_ms = value
            .get("edited_timestamp")
            .and_then(Value::as_str)
            .and_then(parse_rfc3339_millis);

        let thread_root = root_id.map(str::to_string).unwrap_or_else(|| id.clone());

        MessageData {
            id,
            thread_id: format!("discord:{channel_id}:{thread_root}"),
            text,
            author: AuthorData {
                user_id,
                user_name: user_name.clone(),
                full_name: user_name,
                is_bot,
                is_me,
            },
            metadata: MessageMetadataData {
                date_sent_ms,
                edited: edited_at_ms.is_some(),
                edited_at_ms,
            },
            attachments: Vec::new(),
            is_mention: None,
            formatted: None,
            raw: Some(value.clone()),
        }
    }
}

impl Adapter for DiscordAdapter {
    fn name(&self) -> &str {
        "discord"
    }

    fn user_name(&self) -> &str {
        &self.user_name
    }

    fn bot_user_id(&self) -> Option<&str> {
        self.bot_user_id.as_deref()
    }

    fn post_message(
        &self,
        thread_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        let (channel_id, root_message_id) = parse_adapter_thread_id("discord", thread_id)?;
        let mut body = json!({
            "content": message.plain_text()
        });
        if let Some(root_message_id) = root_message_id.clone() {
            body["message_reference"] = json!({
                "message_id": root_message_id
            });
        }

        let payload = self.request(
            Method::POST,
            &format!("channels/{channel_id}/messages"),
            Some(body),
        )?;
        let message_id = payload
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| ChatError::Api("discord create message missing id".to_string()))?
            .to_string();

        let thread_root = root_message_id.unwrap_or_else(|| message_id.clone());

        Ok(RawSentMessage {
            id: message_id,
            thread_id: format!("discord:{channel_id}:{thread_root}"),
        })
    }

    fn post_channel_message(
        &self,
        channel_id: &str,
        message: &PostableMessage,
    ) -> Result<RawSentMessage, ChatError> {
        let channel_id = parse_adapter_channel_id("discord", channel_id)?;
        let payload = self.request(
            Method::POST,
            &format!("channels/{channel_id}/messages"),
            Some(json!({
                "content": message.plain_text()
            })),
        )?;

        let message_id = payload
            .get("id")
            .and_then(Value::as_str)
            .ok_or_else(|| ChatError::Api("discord create message missing id".to_string()))?
            .to_string();

        Ok(RawSentMessage {
            id: message_id.clone(),
            thread_id: format!("discord:{channel_id}:{message_id}"),
        })
    }

    fn edit_message(
        &self,
        thread_id: &str,
        message_id: &str,
        message: &PostableMessage,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("discord", thread_id)?;
        self.request(
            Method::PATCH,
            &format!("channels/{channel_id}/messages/{message_id}"),
            Some(json!({
                "content": message.plain_text()
            })),
        )?;
        Ok(())
    }

    fn delete_message(&self, thread_id: &str, message_id: &str) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("discord", thread_id)?;
        self.request(
            Method::DELETE,
            &format!("channels/{channel_id}/messages/{message_id}"),
            None,
        )?;
        Ok(())
    }

    fn add_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("discord", thread_id)?;
        let emoji = encode(emoji);
        self.request(
            Method::PUT,
            &format!("channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"),
            None,
        )?;
        Ok(())
    }

    fn remove_reaction(
        &self,
        thread_id: &str,
        message_id: &str,
        emoji: &str,
    ) -> Result<(), ChatError> {
        let (channel_id, _) = parse_adapter_thread_id("discord", thread_id)?;
        let emoji = encode(emoji);
        self.request(
            Method::DELETE,
            &format!("channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me"),
            None,
        )?;
        Ok(())
    }

    fn fetch_messages(&self, thread_id: &str, limit: usize) -> Result<Vec<MessageData>, ChatError> {
        let (channel_id, root_message_id) = parse_adapter_thread_id("discord", thread_id)?;
        let bounded_limit = limit.clamp(1, 100);
        let payload = self.request(
            Method::GET,
            &format!("channels/{channel_id}/messages?limit={bounded_limit}"),
            None,
        )?;

        let mut messages = payload
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|value| {
                if let Some(root) = root_message_id.as_deref() {
                    let current_id = value.get("id").and_then(Value::as_str);
                    let reply_to = value
                        .get("message_reference")
                        .and_then(Value::as_object)
                        .and_then(|reference| reference.get("message_id"))
                        .and_then(Value::as_str);
                    current_id == Some(root) || reply_to == Some(root)
                } else {
                    true
                }
            })
            .map(|value| self.parse_message(&channel_id, root_message_id.as_deref(), &value))
            .collect::<Vec<_>>();

        messages.sort_by_key(|message| message.metadata.date_sent_ms);
        Ok(messages)
    }

    fn fetch_message_by_id(
        &self,
        thread_id: &str,
        message_id: &str,
    ) -> Result<Option<MessageData>, ChatError> {
        let (channel_id, root_message_id) = parse_adapter_thread_id("discord", thread_id)?;
        let url = format!(
            "{}/channels/{}/messages/{}",
            self.api_base_url, channel_id, message_id
        );
        let response = self
            .client
            .request(Method::GET, url)
            .header("Authorization", format!("Bot {}", self.bot_token))
            .header("User-Agent", "lsmsg-rs/0.1")
            .send()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        let status = response.status();
        if status == reqwest::StatusCode::NOT_FOUND || status == reqwest::StatusCode::NO_CONTENT {
            return Ok(None);
        }

        let payload: Value = response
            .json()
            .map_err(|err| ChatError::Http(err.to_string()))?;
        if !status.is_success() {
            return Err(ChatError::Http(format!(
                "discord channels/{channel_id}/messages/{message_id} failed with status {}: {}",
                status, payload
            )));
        }

        Ok(Some(self.parse_message(
            &channel_id,
            root_message_id.as_deref(),
            &payload,
        )))
    }
}

struct LangGraphAdapter {
    assistant_id: String,
    api_base_url: String,
    api_key: Option<String>,
    thread_namespace: Uuid,
    client: Client,
}

struct RunDispatchOptions<'a> {
    input: Option<&'a Value>,
    thread_metadata: Option<&'a Value>,
    run_metadata: Option<&'a Value>,
    config: Option<&'a Value>,
    multitask_strategy: &'a str,
    if_not_exists: &'a str,
    webhook: Option<&'a str>,
    durability: Option<&'a str>,
}

impl LangGraphAdapter {
    fn new(
        api_base_url: String,
        assistant_id: String,
        api_key: Option<String>,
        thread_namespace: Option<String>,
    ) -> Result<Self, ChatError> {
        let api_base_url = require_non_empty(api_base_url, "api_base_url")?;
        let assistant_id = require_non_empty(assistant_id, "assistant_id")?;
        let namespace_seed = thread_namespace
            .map(|seed| require_non_empty(seed, "thread_namespace"))
            .transpose()?
            .unwrap_or_else(|| LANGGRAPH_THREAD_NAMESPACE_SEED.to_string());
        let thread_namespace = Uuid::new_v5(&Uuid::NAMESPACE_URL, namespace_seed.as_bytes());
        let client = Client::builder()
            .build()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        Ok(Self {
            assistant_id,
            api_base_url: api_base_url.trim_end_matches('/').to_string(),
            api_key,
            thread_namespace,
            client,
        })
    }

    fn api_post(&self, path: &str, body: Value) -> Result<Value, ChatError> {
        let url = format!("{}/{}", self.api_base_url, path.trim_start_matches('/'));
        let mut request = self.client.post(url).json(&body);
        if let Some(api_key) = &self.api_key {
            request = request.bearer_auth(api_key);
        }

        let response = request
            .send()
            .map_err(|err| ChatError::Http(err.to_string()))?;
        let status = response.status();
        let body_text = response
            .text()
            .map_err(|err| ChatError::Http(err.to_string()))?;

        if !status.is_success() {
            return Err(ChatError::Http(format!(
                "langgraph {} failed with status {}: {}",
                path, status, body_text
            )));
        }

        if body_text.trim().is_empty() {
            return Ok(Value::Null);
        }

        serde_json::from_str(&body_text).map_err(|err| {
            ChatError::Http(format!(
                "langgraph {} returned invalid json body ({}): {}",
                path, err, body_text
            ))
        })
    }

    fn thread_id_for_external(
        &self,
        provider: &str,
        workspace_id: &str,
        channel_id: &str,
        root_thread_id: &str,
    ) -> Result<String, ChatError> {
        let key =
            self.canonical_external_thread_key(provider, workspace_id, channel_id, root_thread_id)?;
        Ok(Uuid::new_v5(&self.thread_namespace, key.as_bytes()).to_string())
    }

    fn ensure_thread(
        &self,
        thread_id: &str,
        metadata: Option<&Value>,
        if_exists: &str,
    ) -> Result<Value, ChatError> {
        validate_uuid_string(thread_id, "thread_id")?;
        if if_exists != "raise" && if_exists != "do_nothing" {
            return Err(ChatError::InvalidArgument(
                "if_exists must be 'raise' or 'do_nothing'".to_string(),
            ));
        }

        let metadata = metadata.map(require_json_object).transpose()?;
        let mut body = json!({
            "thread_id": thread_id,
            "if_exists": if_exists
        });
        if let Some(metadata) = metadata {
            body["metadata"] = Value::Object(metadata);
        }

        self.api_post("threads", body)
    }

    fn create_run(
        &self,
        thread_id: &str,
        input: Option<&Value>,
        metadata: Option<&Value>,
        config: Option<&Value>,
        multitask_strategy: &str,
        if_not_exists: &str,
        webhook: Option<&str>,
        durability: Option<&str>,
    ) -> Result<Value, ChatError> {
        validate_uuid_string(thread_id, "thread_id")?;
        if !matches!(
            multitask_strategy,
            "reject" | "rollback" | "interrupt" | "enqueue"
        ) {
            return Err(ChatError::InvalidArgument(
                "multitask_strategy must be one of: reject, rollback, interrupt, enqueue"
                    .to_string(),
            ));
        }
        if !matches!(if_not_exists, "create" | "reject") {
            return Err(ChatError::InvalidArgument(
                "if_not_exists must be 'create' or 'reject'".to_string(),
            ));
        }
        if let Some(durability) = durability {
            if !matches!(durability, "sync" | "async" | "exit") {
                return Err(ChatError::InvalidArgument(
                    "durability must be one of: sync, async, exit".to_string(),
                ));
            }
        }

        let metadata = metadata.map(require_json_object).transpose()?;
        let config = config.map(require_json_object).transpose()?;
        let mut body = json!({
            "assistant_id": self.assistant_id,
            "multitask_strategy": multitask_strategy,
            "if_not_exists": if_not_exists
        });

        if let Some(input) = input {
            body["input"] = input.clone();
        }
        if let Some(metadata) = metadata {
            body["metadata"] = Value::Object(metadata);
        }
        if let Some(config) = config {
            body["config"] = Value::Object(config);
        }
        if let Some(webhook) = webhook {
            let webhook = require_non_empty(webhook.to_string(), "webhook")?;
            body["webhook"] = Value::String(webhook);
        }
        if let Some(durability) = durability {
            body["durability"] = Value::String(durability.to_string());
        }

        self.api_post(&format!("threads/{thread_id}/runs"), body)
    }

    fn trigger_external_run(
        &self,
        provider: &str,
        workspace_id: &str,
        channel_id: &str,
        root_thread_id: &str,
        options: RunDispatchOptions<'_>,
    ) -> Result<Value, ChatError> {
        let (provider, workspace_id, channel_id, root_thread_id, key) =
            self.normalized_external_parts(provider, workspace_id, channel_id, root_thread_id)?;
        let thread_id = Uuid::new_v5(&self.thread_namespace, key.as_bytes()).to_string();

        let mut thread_metadata = serde_json::Map::new();
        thread_metadata.insert("chat_provider".to_string(), Value::String(provider));
        thread_metadata.insert("chat_workspace_id".to_string(), Value::String(workspace_id));
        thread_metadata.insert("chat_channel_id".to_string(), Value::String(channel_id));
        thread_metadata.insert(
            "chat_root_thread_id".to_string(),
            Value::String(root_thread_id),
        );
        thread_metadata.insert("chat_external_thread_key".to_string(), Value::String(key));

        if let Some(extra_metadata) = options.thread_metadata {
            for (key, value) in require_json_object(extra_metadata)? {
                thread_metadata.insert(key, value);
            }
        }

        let thread = self.ensure_thread(
            &thread_id,
            Some(&Value::Object(thread_metadata)),
            "do_nothing",
        )?;
        let run = self.create_run(
            &thread_id,
            options.input,
            options.run_metadata,
            options.config,
            options.multitask_strategy,
            options.if_not_exists,
            options.webhook,
            options.durability,
        )?;

        Ok(json!({
            "thread_id": thread_id,
            "thread": thread,
            "run": run
        }))
    }

    fn canonical_external_thread_key(
        &self,
        provider: &str,
        workspace_id: &str,
        channel_id: &str,
        root_thread_id: &str,
    ) -> Result<String, ChatError> {
        let (_, _, _, _, key) =
            self.normalized_external_parts(provider, workspace_id, channel_id, root_thread_id)?;
        Ok(key)
    }

    fn normalized_external_parts(
        &self,
        provider: &str,
        workspace_id: &str,
        channel_id: &str,
        root_thread_id: &str,
    ) -> Result<(String, String, String, String, String), ChatError> {
        let provider = require_non_empty(provider.to_string(), "provider")?.to_ascii_lowercase();
        let workspace_id = require_non_empty(workspace_id.to_string(), "workspace_id")?;
        let channel_id = require_non_empty(channel_id.to_string(), "channel_id")?;
        let root_thread_id = require_non_empty(root_thread_id.to_string(), "root_thread_id")?;
        let key = format!(
            "provider={provider}|workspace={workspace_id}|channel={channel_id}|root={root_thread_id}"
        );

        Ok((provider, workspace_id, channel_id, root_thread_id, key))
    }
}

struct PatternHandler {
    pattern: Regex,
    callback: Py<PyAny>,
}

struct ChatCore {
    user_name: String,
    dedupe_ttl: Duration,
    lock_ttl: Duration,
    state: MemoryState,
    adapters: RwLock<HashMap<String, Arc<dyn Adapter>>>,
    mention_handlers: RwLock<Vec<Py<PyAny>>>,
    subscribed_handlers: RwLock<Vec<Py<PyAny>>>,
    message_handlers: RwLock<Vec<PatternHandler>>,
}

impl ChatCore {
    fn new(user_name: String, dedupe_ttl_ms: u64, lock_ttl_ms: u64) -> Self {
        Self {
            user_name,
            dedupe_ttl: Duration::from_millis(dedupe_ttl_ms),
            lock_ttl: Duration::from_millis(lock_ttl_ms),
            state: MemoryState::default(),
            adapters: RwLock::new(HashMap::new()),
            mention_handlers: RwLock::new(Vec::new()),
            subscribed_handlers: RwLock::new(Vec::new()),
            message_handlers: RwLock::new(Vec::new()),
        }
    }

    fn add_adapter(&self, adapter: Arc<dyn Adapter>) -> Result<(), ChatError> {
        let mut adapters = self.adapters.write();
        if adapters.contains_key(adapter.name()) {
            return Err(ChatError::AdapterAlreadyExists(adapter.name().to_string()));
        }
        adapters.insert(adapter.name().to_string(), adapter);
        Ok(())
    }

    fn adapter(&self, name: &str) -> Result<Arc<dyn Adapter>, ChatError> {
        self.adapters
            .read()
            .get(name)
            .cloned()
            .ok_or_else(|| ChatError::AdapterNotFound(name.to_string()))
    }

    fn register_mention_handler(&self, callback: Py<PyAny>) {
        self.mention_handlers.write().push(callback);
    }

    fn register_subscribed_handler(&self, callback: Py<PyAny>) {
        self.subscribed_handlers.write().push(callback);
    }

    fn register_message_handler(
        &self,
        pattern: &str,
        callback: Py<PyAny>,
    ) -> Result<(), ChatError> {
        let regex =
            Regex::new(pattern).map_err(|err| ChatError::InvalidPattern(err.to_string()))?;
        self.message_handlers.write().push(PatternHandler {
            pattern: regex,
            callback,
        });
        Ok(())
    }

    fn detect_mention(&self, adapter: &dyn Adapter, message: &MessageData) -> bool {
        let text = message.text.as_str();
        let handle = adapter.user_name();
        if contains_mention(text, handle) {
            return true;
        }
        if handle != self.user_name && contains_mention(text, &self.user_name) {
            return true;
        }

        if let Some(bot_user_id) = adapter.bot_user_id() {
            if contains_mention(text, bot_user_id)
                || text.contains(&format!("<@{}>", bot_user_id))
                || text.contains(&format!("<@!{}>", bot_user_id))
            {
                return true;
            }
        }

        false
    }
}

#[pyclass(name = "Author", module = "lsmsg_rs._lsmsg_rs")]
#[derive(Clone)]
struct PyAuthor {
    inner: AuthorData,
}

#[pymethods]
impl PyAuthor {
    #[new]
    #[pyo3(signature = (user_id, user_name=None, full_name=None, is_bot=false, is_me=false))]
    fn new(
        user_id: String,
        user_name: Option<String>,
        full_name: Option<String>,
        is_bot: bool,
        is_me: bool,
    ) -> Self {
        let user_name = user_name.unwrap_or_else(|| user_id.clone());
        let full_name = full_name.unwrap_or_else(|| user_name.clone());

        Self {
            inner: AuthorData {
                user_id,
                user_name,
                full_name,
                is_bot,
                is_me,
            },
        }
    }

    #[getter]
    fn user_id(&self) -> String {
        self.inner.user_id.clone()
    }

    #[getter]
    fn user_name(&self) -> String {
        self.inner.user_name.clone()
    }

    #[getter]
    fn full_name(&self) -> String {
        self.inner.full_name.clone()
    }

    #[getter]
    fn is_bot(&self) -> bool {
        self.inner.is_bot
    }

    #[getter]
    fn is_me(&self) -> bool {
        self.inner.is_me
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        Ok(pythonize(py, &self.inner)?.into())
    }

    fn __repr__(&self) -> String {
        format!(
            "Author(user_id='{}', user_name='{}', is_me={})",
            self.inner.user_id, self.inner.user_name, self.inner.is_me
        )
    }
}

#[pyclass(name = "Message", module = "lsmsg_rs._lsmsg_rs")]
#[derive(Clone)]
struct PyMessage {
    inner: MessageData,
}

#[pymethods]
impl PyMessage {
    #[new]
    #[pyo3(signature = (id, thread_id, text, author, *, is_mention=None, raw=None, formatted=None, date_sent_ms=None))]
    fn new(
        py: Python<'_>,
        id: String,
        thread_id: String,
        text: String,
        author: PyRef<'_, PyAuthor>,
        is_mention: Option<bool>,
        raw: Option<PyObject>,
        formatted: Option<PyObject>,
        date_sent_ms: Option<i64>,
    ) -> PyResult<Self> {
        let raw = raw
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;

        let formatted = formatted
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;

        Ok(Self {
            inner: MessageData {
                id,
                thread_id,
                text,
                author: author.inner.clone(),
                metadata: MessageMetadataData {
                    date_sent_ms: date_sent_ms.unwrap_or_else(now_millis),
                    edited: false,
                    edited_at_ms: None,
                },
                attachments: Vec::new(),
                is_mention,
                formatted,
                raw,
            },
        })
    }

    #[getter]
    fn id(&self) -> String {
        self.inner.id.clone()
    }

    #[getter]
    fn thread_id(&self) -> String {
        self.inner.thread_id.clone()
    }

    #[getter]
    fn text(&self) -> String {
        self.inner.text.clone()
    }

    #[getter]
    fn is_mention(&self) -> Option<bool> {
        self.inner.is_mention
    }

    #[getter]
    fn author(&self) -> PyAuthor {
        PyAuthor {
            inner: self.inner.author.clone(),
        }
    }

    fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        Ok(pythonize(py, &self.inner)?.into())
    }

    fn __repr__(&self) -> String {
        format!(
            "Message(id='{}', thread_id='{}', text='{}')",
            self.inner.id, self.inner.thread_id, self.inner.text
        )
    }
}

#[pyclass(name = "SentMessage", module = "lsmsg_rs._lsmsg_rs")]
struct PySentMessage {
    core: Arc<ChatCore>,
    adapter_name: String,
    id: String,
    thread_id: String,
    text: String,
}

#[pymethods]
impl PySentMessage {
    #[getter]
    fn id(&self) -> String {
        self.id.clone()
    }

    #[getter]
    fn thread_id(&self) -> String {
        self.thread_id.clone()
    }

    #[getter]
    fn text(&self) -> String {
        self.text.clone()
    }

    fn edit(&mut self, _py: Python<'_>, message: &Bound<'_, PyAny>) -> PyResult<()> {
        let postable = parse_postable(message)?;
        self.text = postable.plain_text();
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter.edit_message(&self.thread_id, &self.id, &postable)?;
        Ok(())
    }

    fn delete(&self) -> PyResult<()> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter.delete_message(&self.thread_id, &self.id)?;
        Ok(())
    }

    fn add_reaction(&self, emoji: String) -> PyResult<()> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter.add_reaction(&self.thread_id, &self.id, &emoji)?;
        Ok(())
    }

    fn remove_reaction(&self, emoji: String) -> PyResult<()> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter.remove_reaction(&self.thread_id, &self.id, &emoji)?;
        Ok(())
    }

    fn to_message(&self, py: Python<'_>) -> PyResult<Py<PyMessage>> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        let maybe_message = adapter.fetch_message_by_id(&self.thread_id, &self.id)?;

        let message = maybe_message.ok_or_else(|| ChatError::MessageNotFound(self.id.clone()))?;
        Py::new(py, PyMessage { inner: message })
    }

    fn __repr__(&self) -> String {
        format!(
            "SentMessage(id='{}', thread_id='{}', text='{}')",
            self.id, self.thread_id, self.text
        )
    }
}

#[pyclass(name = "Thread", module = "lsmsg_rs._lsmsg_rs")]
struct PyThread {
    core: Arc<ChatCore>,
    adapter_name: String,
    id: String,
    channel_id: String,
    is_dm: bool,
}

#[pymethods]
impl PyThread {
    #[getter]
    fn id(&self) -> String {
        self.id.clone()
    }

    #[getter]
    fn channel_id(&self) -> String {
        self.channel_id.clone()
    }

    #[getter]
    fn adapter_name(&self) -> String {
        self.adapter_name.clone()
    }

    #[getter]
    fn is_dm(&self) -> bool {
        self.is_dm
    }

    fn subscribe(&self) {
        self.core.state.subscribe(&self.id);
    }

    fn unsubscribe(&self) {
        self.core.state.unsubscribe(&self.id);
    }

    fn is_subscribed(&self) -> bool {
        self.core.state.is_subscribed(&self.id)
    }

    fn post(&self, py: Python<'_>, message: &Bound<'_, PyAny>) -> PyResult<Py<PySentMessage>> {
        let postable = parse_postable(message)?;
        let adapter = self.core.adapter(&self.adapter_name)?;
        let raw = adapter.post_message(&self.id, &postable)?;

        Py::new(
            py,
            PySentMessage {
                core: self.core.clone(),
                adapter_name: self.adapter_name.clone(),
                id: raw.id,
                thread_id: raw.thread_id,
                text: postable.plain_text(),
            },
        )
    }

    #[pyo3(signature = (limit=50))]
    fn recent_messages(&self, py: Python<'_>, limit: usize) -> PyResult<Vec<Py<PyMessage>>> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter
            .fetch_messages(&self.id, limit)?
            .into_iter()
            .map(|message| Py::new(py, PyMessage { inner: message }))
            .collect()
    }

    #[pyo3(signature = (limit=50))]
    fn refresh(&self, py: Python<'_>, limit: usize) -> PyResult<Vec<Py<PyMessage>>> {
        self.recent_messages(py, limit)
    }

    #[getter]
    fn state(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        let key = format!("thread-state:{}", self.id);
        self.core
            .state
            .get(&key)
            .map(|value| pythonize(py, &value).map(Into::into))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()).into())
    }

    #[pyo3(signature = (state, replace=false))]
    fn set_state(&self, state: &Bound<'_, PyAny>, replace: bool) -> PyResult<()> {
        let incoming: Value = depythonize(state).map_err(|err| {
            PyTypeError::new_err(format!("state must be JSON-serializable: {err}"))
        })?;

        let key = format!("thread-state:{}", self.id);

        if replace {
            self.core.state.set(&key, incoming, Some(THREAD_STATE_TTL));
            return Ok(());
        }

        let merged = if let Some(existing) = self.core.state.get(&key) {
            merge_json(existing, incoming)
        } else {
            incoming
        };

        self.core.state.set(&key, merged, Some(THREAD_STATE_TTL));
        Ok(())
    }

    fn mention_user(&self, user_id: String) -> PyResult<String> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        Ok(adapter.mention_user(&user_id))
    }

    fn channel(&self, py: Python<'_>) -> PyResult<Py<PyChannel>> {
        Py::new(
            py,
            PyChannel {
                core: self.core.clone(),
                adapter_name: self.adapter_name.clone(),
                id: self.channel_id.clone(),
                is_dm: self.is_dm,
            },
        )
    }

    fn __repr__(&self) -> String {
        format!(
            "Thread(id='{}', channel_id='{}', adapter='{}')",
            self.id, self.channel_id, self.adapter_name
        )
    }
}

#[pyclass(name = "Channel", module = "lsmsg_rs._lsmsg_rs")]
struct PyChannel {
    core: Arc<ChatCore>,
    adapter_name: String,
    id: String,
    is_dm: bool,
}

#[pymethods]
impl PyChannel {
    #[getter]
    fn id(&self) -> String {
        self.id.clone()
    }

    #[getter]
    fn adapter_name(&self) -> String {
        self.adapter_name.clone()
    }

    #[getter]
    fn is_dm(&self) -> bool {
        self.is_dm
    }

    fn post(&self, py: Python<'_>, message: &Bound<'_, PyAny>) -> PyResult<Py<PySentMessage>> {
        let postable = parse_postable(message)?;
        let adapter = self.core.adapter(&self.adapter_name)?;
        let raw = adapter.post_channel_message(&self.id, &postable)?;

        Py::new(
            py,
            PySentMessage {
                core: self.core.clone(),
                adapter_name: self.adapter_name.clone(),
                id: raw.id,
                thread_id: raw.thread_id,
                text: postable.plain_text(),
            },
        )
    }

    #[pyo3(signature = (limit=50))]
    fn messages(&self, py: Python<'_>, limit: usize) -> PyResult<Vec<Py<PyMessage>>> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        adapter
            .fetch_messages(&self.id, limit)?
            .into_iter()
            .map(|message| Py::new(py, PyMessage { inner: message }))
            .collect()
    }

    #[getter]
    fn state(&self, py: Python<'_>) -> PyResult<Option<PyObject>> {
        let key = format!("channel-state:{}", self.id);
        self.core
            .state
            .get(&key)
            .map(|value| pythonize(py, &value).map(Into::into))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()).into())
    }

    #[pyo3(signature = (state, replace=false))]
    fn set_state(&self, state: &Bound<'_, PyAny>, replace: bool) -> PyResult<()> {
        let incoming: Value = depythonize(state).map_err(|err| {
            PyTypeError::new_err(format!("state must be JSON-serializable: {err}"))
        })?;

        let key = format!("channel-state:{}", self.id);

        if replace {
            self.core.state.set(&key, incoming, Some(CHANNEL_STATE_TTL));
            return Ok(());
        }

        let merged = if let Some(existing) = self.core.state.get(&key) {
            merge_json(existing, incoming)
        } else {
            incoming
        };

        self.core.state.set(&key, merged, Some(CHANNEL_STATE_TTL));
        Ok(())
    }

    fn mention_user(&self, user_id: String) -> PyResult<String> {
        let adapter = self.core.adapter(&self.adapter_name)?;
        Ok(adapter.mention_user(&user_id))
    }

    fn __repr__(&self) -> String {
        format!("Channel(id='{}', adapter='{}')", self.id, self.adapter_name)
    }
}

#[pyclass(name = "InMemoryAdapter", module = "lsmsg_rs._lsmsg_rs")]
struct PyInMemoryAdapter {
    inner: Arc<InMemoryAdapter>,
}

#[pymethods]
impl PyInMemoryAdapter {
    #[new]
    #[pyo3(signature = (name, user_name, bot_user_id=None))]
    fn new(name: String, user_name: String, bot_user_id: Option<String>) -> Self {
        Self {
            inner: Arc::new(InMemoryAdapter::new(name, user_name, bot_user_id)),
        }
    }

    #[getter]
    fn name(&self) -> String {
        self.inner.name.clone()
    }

    #[getter]
    fn user_name(&self) -> String {
        self.inner.user_name.clone()
    }

    #[getter]
    fn bot_user_id(&self) -> Option<String> {
        self.inner.bot_user_id.clone()
    }

    #[pyo3(signature = (thread_id, limit=50))]
    fn fetch_messages(
        &self,
        py: Python<'_>,
        thread_id: String,
        limit: usize,
    ) -> PyResult<Vec<Py<PyMessage>>> {
        self.inner
            .fetch_messages(&thread_id, limit)?
            .into_iter()
            .map(|message| Py::new(py, PyMessage { inner: message }))
            .collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "InMemoryAdapter(name='{}', user_name='{}')",
            self.inner.name, self.inner.user_name
        )
    }
}

#[pyclass(name = "SlackAdapter", module = "lsmsg_rs._lsmsg_rs")]
struct PySlackAdapter {
    inner: Arc<SlackAdapter>,
}

#[pymethods]
impl PySlackAdapter {
    #[new]
    #[pyo3(signature = (*, bot_token, user_name, bot_user_id=None, api_base_url=None))]
    fn new(
        bot_token: String,
        user_name: String,
        bot_user_id: Option<String>,
        api_base_url: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(SlackAdapter::new(
                bot_token,
                user_name,
                bot_user_id,
                api_base_url,
            )?),
        })
    }

    #[getter]
    fn name(&self) -> &'static str {
        "slack"
    }

    #[getter]
    fn user_name(&self) -> String {
        self.inner.user_name.clone()
    }

    #[getter]
    fn bot_user_id(&self) -> Option<String> {
        self.inner.bot_user_id.clone()
    }

    fn __repr__(&self) -> String {
        format!("SlackAdapter(user_name='{}')", self.inner.user_name)
    }
}

#[pyclass(name = "DiscordAdapter", module = "lsmsg_rs._lsmsg_rs")]
struct PyDiscordAdapter {
    inner: Arc<DiscordAdapter>,
}

#[pymethods]
impl PyDiscordAdapter {
    #[new]
    #[pyo3(signature = (*, bot_token, user_name, bot_user_id=None, api_base_url=None))]
    fn new(
        bot_token: String,
        user_name: String,
        bot_user_id: Option<String>,
        api_base_url: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(DiscordAdapter::new(
                bot_token,
                user_name,
                bot_user_id,
                api_base_url,
            )?),
        })
    }

    #[getter]
    fn name(&self) -> &'static str {
        "discord"
    }

    #[getter]
    fn user_name(&self) -> String {
        self.inner.user_name.clone()
    }

    #[getter]
    fn bot_user_id(&self) -> Option<String> {
        self.inner.bot_user_id.clone()
    }

    fn __repr__(&self) -> String {
        format!("DiscordAdapter(user_name='{}')", self.inner.user_name)
    }
}

#[pyclass(name = "LangGraphAdapter", module = "lsmsg_rs._lsmsg_rs")]
struct PyLangGraphAdapter {
    inner: Arc<LangGraphAdapter>,
}

#[pymethods]
impl PyLangGraphAdapter {
    #[new]
    #[pyo3(signature = (*, api_base_url, assistant_id, api_key=None, thread_namespace=None))]
    fn new(
        api_base_url: String,
        assistant_id: String,
        api_key: Option<String>,
        thread_namespace: Option<String>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(LangGraphAdapter::new(
                api_base_url,
                assistant_id,
                api_key,
                thread_namespace,
            )?),
        })
    }

    #[getter]
    fn name(&self) -> &'static str {
        "langgraph"
    }

    #[getter]
    fn assistant_id(&self) -> String {
        self.inner.assistant_id.clone()
    }

    #[getter]
    fn api_base_url(&self) -> String {
        self.inner.api_base_url.clone()
    }

    #[pyo3(signature = (*, provider, workspace_id, channel_id, root_thread_id))]
    fn thread_id(
        &self,
        provider: String,
        workspace_id: String,
        channel_id: String,
        root_thread_id: String,
    ) -> PyResult<String> {
        self.inner
            .thread_id_for_external(&provider, &workspace_id, &channel_id, &root_thread_id)
            .map_err(Into::into)
    }

    #[pyo3(signature = (thread_id, *, metadata=None, if_exists="do_nothing"))]
    fn ensure_thread(
        &self,
        py: Python<'_>,
        thread_id: String,
        metadata: Option<PyObject>,
        if_exists: &str,
    ) -> PyResult<PyObject> {
        let metadata = metadata
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let thread = self
            .inner
            .ensure_thread(&thread_id, metadata.as_ref(), if_exists)?;
        Ok(pythonize(py, &thread)?.into())
    }

    #[pyo3(signature = (thread_id, *, input=None, metadata=None, config=None, multitask_strategy="enqueue", if_not_exists="create", webhook=None, durability=None))]
    fn create_run(
        &self,
        py: Python<'_>,
        thread_id: String,
        input: Option<PyObject>,
        metadata: Option<PyObject>,
        config: Option<PyObject>,
        multitask_strategy: &str,
        if_not_exists: &str,
        webhook: Option<String>,
        durability: Option<String>,
    ) -> PyResult<PyObject> {
        let input = input
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let metadata = metadata
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let config = config
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;

        let run = self.inner.create_run(
            &thread_id,
            input.as_ref(),
            metadata.as_ref(),
            config.as_ref(),
            multitask_strategy,
            if_not_exists,
            webhook.as_deref(),
            durability.as_deref(),
        )?;
        Ok(pythonize(py, &run)?.into())
    }

    #[pyo3(signature = (*, provider, workspace_id, channel_id, root_thread_id, input=None, thread_metadata=None, run_metadata=None, config=None, multitask_strategy="enqueue", if_not_exists="create", webhook=None, durability=None))]
    fn trigger_run(
        &self,
        py: Python<'_>,
        provider: String,
        workspace_id: String,
        channel_id: String,
        root_thread_id: String,
        input: Option<PyObject>,
        thread_metadata: Option<PyObject>,
        run_metadata: Option<PyObject>,
        config: Option<PyObject>,
        multitask_strategy: &str,
        if_not_exists: &str,
        webhook: Option<String>,
        durability: Option<String>,
    ) -> PyResult<PyObject> {
        let input = input
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let thread_metadata = thread_metadata
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let run_metadata = run_metadata
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;
        let config = config
            .as_ref()
            .map(|value| depythonize::<Value>(value.bind(py)))
            .transpose()
            .map_err(|err| ChatError::Serialization(err.to_string()))?;

        let result = self.inner.trigger_external_run(
            &provider,
            &workspace_id,
            &channel_id,
            &root_thread_id,
            RunDispatchOptions {
                input: input.as_ref(),
                thread_metadata: thread_metadata.as_ref(),
                run_metadata: run_metadata.as_ref(),
                config: config.as_ref(),
                multitask_strategy,
                if_not_exists,
                webhook: webhook.as_deref(),
                durability: durability.as_deref(),
            },
        )?;
        Ok(pythonize(py, &result)?.into())
    }

    fn __repr__(&self) -> String {
        format!(
            "LangGraphAdapter(assistant_id='{}', api_base_url='{}')",
            self.inner.assistant_id, self.inner.api_base_url
        )
    }
}

#[pyclass(name = "Chat", module = "lsmsg_rs._lsmsg_rs")]
struct PyChat {
    core: Arc<ChatCore>,
}

#[pymethods]
impl PyChat {
    #[new]
    #[pyo3(signature = (*, user_name, dedupe_ttl_ms=300_000, lock_ttl_ms=30_000))]
    fn new(user_name: String, dedupe_ttl_ms: u64, lock_ttl_ms: u64) -> Self {
        Self {
            core: Arc::new(ChatCore::new(user_name, dedupe_ttl_ms, lock_ttl_ms)),
        }
    }

    fn add_adapter(&self, adapter: &Bound<'_, PyAny>) -> PyResult<()> {
        if let Ok(in_memory) = adapter.extract::<PyRef<'_, PyInMemoryAdapter>>() {
            let adapter_trait: Arc<dyn Adapter> = in_memory.inner.clone();
            self.core.add_adapter(adapter_trait)?;
            return Ok(());
        }

        if let Ok(slack) = adapter.extract::<PyRef<'_, PySlackAdapter>>() {
            let adapter_trait: Arc<dyn Adapter> = slack.inner.clone();
            self.core.add_adapter(adapter_trait)?;
            return Ok(());
        }

        if let Ok(discord) = adapter.extract::<PyRef<'_, PyDiscordAdapter>>() {
            let adapter_trait: Arc<dyn Adapter> = discord.inner.clone();
            self.core.add_adapter(adapter_trait)?;
            return Ok(());
        }

        Err(PyTypeError::new_err(
            "adapter must be InMemoryAdapter, SlackAdapter, or DiscordAdapter",
        ))
    }

    fn on_new_mention(&self, py: Python<'_>, callback: Py<PyAny>) -> PyResult<()> {
        ensure_callable(callback.bind(py).as_any())?;
        self.core.register_mention_handler(callback);
        Ok(())
    }

    fn on_subscribed_message(&self, py: Python<'_>, callback: Py<PyAny>) -> PyResult<()> {
        ensure_callable(callback.bind(py).as_any())?;
        self.core.register_subscribed_handler(callback);
        Ok(())
    }

    fn on_new_message(&self, py: Python<'_>, pattern: String, callback: Py<PyAny>) -> PyResult<()> {
        ensure_callable(callback.bind(py).as_any())?;
        self.core.register_message_handler(&pattern, callback)?;
        Ok(())
    }

    #[pyo3(signature = (adapter_name, thread_id, is_dm=None))]
    fn thread(
        &self,
        py: Python<'_>,
        adapter_name: String,
        thread_id: String,
        is_dm: Option<bool>,
    ) -> PyResult<Py<PyThread>> {
        let adapter = self.core.adapter(&adapter_name)?;
        Py::new(
            py,
            PyThread {
                core: self.core.clone(),
                adapter_name,
                channel_id: adapter.channel_id_from_thread_id(&thread_id),
                is_dm: is_dm.unwrap_or_else(|| adapter.is_dm(&thread_id)),
                id: thread_id,
            },
        )
    }

    fn channel(&self, py: Python<'_>, channel_id: String) -> PyResult<Py<PyChannel>> {
        let adapter_name = channel_id
            .split(':')
            .next()
            .ok_or_else(|| ChatError::InvalidChannelId(channel_id.clone()))?
            .to_string();
        if adapter_name.is_empty() {
            return Err(ChatError::InvalidChannelId(channel_id).into());
        }

        let adapter = self.core.adapter(&adapter_name)?;

        Py::new(
            py,
            PyChannel {
                core: self.core.clone(),
                adapter_name,
                id: channel_id.clone(),
                is_dm: adapter.is_dm(&channel_id),
            },
        )
    }

    fn process_message(
        &self,
        py: Python<'_>,
        adapter_name: String,
        thread_id: String,
        message: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let adapter = self.core.adapter(&adapter_name)?;
        let mut message_data = extract_message(message, &thread_id)?;

        if message_data.author.is_me {
            return Ok(());
        }

        let dedupe_key = format!("dedupe:{}:{}:{}", adapter_name, thread_id, message_data.id);
        if let Some(Value::Bool(already_processed)) = self.core.state.get(&dedupe_key) {
            if already_processed {
                return Ok(());
            }
        }

        let lock = self
            .core
            .state
            .acquire_lock(&thread_id, self.core.lock_ttl)
            .ok_or_else(|| ChatError::LockBusy(thread_id.clone()))?;

        let result = self.process_message_with_lock(
            py,
            &adapter_name,
            &thread_id,
            &*adapter,
            &mut message_data,
        );
        self.core.state.release_lock(&lock);
        if result.is_ok() {
            self.core
                .state
                .set(&dedupe_key, Value::Bool(true), Some(self.core.dedupe_ttl));
        }
        result
    }

    fn __repr__(&self) -> String {
        format!("Chat(user_name='{}')", self.core.user_name)
    }
}

impl PyChat {
    fn process_message_with_lock(
        &self,
        py: Python<'_>,
        adapter_name: &str,
        thread_id: &str,
        adapter: &dyn Adapter,
        message: &mut MessageData,
    ) -> PyResult<()> {
        let mention = message
            .is_mention
            .unwrap_or_else(|| self.core.detect_mention(adapter, message));
        message.is_mention = Some(mention);

        let thread = Py::new(
            py,
            PyThread {
                core: self.core.clone(),
                adapter_name: adapter_name.to_string(),
                id: thread_id.to_string(),
                channel_id: adapter.channel_id_from_thread_id(thread_id),
                is_dm: adapter.is_dm(thread_id),
            },
        )?;

        let message_obj = Py::new(
            py,
            PyMessage {
                inner: message.clone(),
            },
        )?;

        if self.core.state.is_subscribed(thread_id) {
            let handlers: Vec<Py<PyAny>> = self
                .core
                .subscribed_handlers
                .read()
                .iter()
                .map(|handler| handler.clone_ref(py))
                .collect();
            return invoke_handlers(py, handlers, thread, message_obj);
        }

        if mention {
            let handlers: Vec<Py<PyAny>> = self
                .core
                .mention_handlers
                .read()
                .iter()
                .map(|handler| handler.clone_ref(py))
                .collect();
            return invoke_handlers(py, handlers, thread, message_obj);
        }

        let handlers: Vec<(Regex, Py<PyAny>)> = self
            .core
            .message_handlers
            .read()
            .iter()
            .map(|handler| (handler.pattern.clone(), handler.callback.clone_ref(py)))
            .collect();

        for (pattern, callback) in handlers {
            if pattern.is_match(&message.text) {
                callback.call1(py, (thread.clone_ref(py), message_obj.clone_ref(py)))?;
            }
        }

        Ok(())
    }
}

fn invoke_handlers(
    py: Python<'_>,
    handlers: Vec<Py<PyAny>>,
    thread: Py<PyThread>,
    message: Py<PyMessage>,
) -> PyResult<()> {
    for handler in handlers {
        handler.call1(py, (thread.clone_ref(py), message.clone_ref(py)))?;
    }
    Ok(())
}

fn ensure_callable(callback: &Bound<'_, PyAny>) -> PyResult<()> {
    if callback.is_callable() {
        Ok(())
    } else {
        Err(PyTypeError::new_err("callback must be callable"))
    }
}

fn extract_message(message: &Bound<'_, PyAny>, fallback_thread_id: &str) -> PyResult<MessageData> {
    if let Ok(py_message) = message.extract::<PyRef<'_, PyMessage>>() {
        let mut result = py_message.inner.clone();
        if result.thread_id.is_empty() {
            result.thread_id = fallback_thread_id.to_string();
        }
        normalize_message(&mut result);
        return Ok(result);
    }

    if message.downcast::<PyDict>().is_ok() {
        let mut result: MessageData = depythonize(message).map_err(|err| {
            PyTypeError::new_err(format!("message dict must be JSON-serializable: {err}"))
        })?;
        if result.thread_id.is_empty() {
            result.thread_id = fallback_thread_id.to_string();
        }
        normalize_message(&mut result);
        return Ok(result);
    }

    if let Ok(text) = message.extract::<String>() {
        return Ok(MessageData {
            id: format!("incoming_{}", Uuid::new_v4()),
            thread_id: fallback_thread_id.to_string(),
            text,
            author: AuthorData::default(),
            metadata: MessageMetadataData::default(),
            attachments: Vec::new(),
            is_mention: None,
            formatted: None,
            raw: None,
        });
    }

    Err(PyTypeError::new_err(
        "message must be a Message object, dict, or string",
    ))
}

fn normalize_message(message: &mut MessageData) {
    if message.id.is_empty() {
        message.id = format!("incoming_{}", Uuid::new_v4());
    }
    if message.metadata.date_sent_ms <= 0 {
        message.metadata.date_sent_ms = now_millis();
    }
    if message.author.user_name.is_empty() {
        message.author.user_name = message.author.user_id.clone();
    }
    if message.author.full_name.is_empty() {
        message.author.full_name = message.author.user_name.clone();
    }
}

fn parse_postable(message: &Bound<'_, PyAny>) -> PyResult<PostableMessage> {
    if let Ok(text) = message.extract::<String>() {
        return Ok(PostableMessage::Text(text));
    }

    if let Ok(dict) = message.downcast::<PyDict>() {
        if let Some(markdown) = dict.get_item("markdown")? {
            return Ok(PostableMessage::Markdown(markdown.extract()?));
        }

        if let Some(raw) = dict.get_item("raw")? {
            return Ok(PostableMessage::Raw(raw.extract()?));
        }

        if let Some(text) = dict.get_item("text")? {
            return Ok(PostableMessage::Text(text.extract()?));
        }

        let value: Value = depythonize(dict).map_err(|err| {
            PyTypeError::new_err(format!("message dict must be JSON-serializable: {err}"))
        })?;
        return Ok(PostableMessage::Json(value));
    }

    let value: Value = depythonize(message)
        .map_err(|err| PyTypeError::new_err(format!("message must be str or dict: {err}")))?;
    Ok(PostableMessage::Json(value))
}

fn parse_adapter_thread_id(
    expected_adapter: &str,
    thread_id: &str,
) -> Result<(String, Option<String>), ChatError> {
    let mut parts = thread_id.splitn(3, ':');
    let adapter = parts.next().unwrap_or_default();
    let channel = parts.next().unwrap_or_default();
    let thread = parts
        .next()
        .map(str::to_string)
        .filter(|value| !value.is_empty());

    if adapter != expected_adapter || channel.is_empty() {
        return Err(ChatError::InvalidThreadId(thread_id.to_string()));
    }

    Ok((channel.to_string(), thread))
}

fn parse_adapter_channel_id(expected_adapter: &str, channel_id: &str) -> Result<String, ChatError> {
    let mut parts = channel_id.splitn(3, ':');
    let adapter = parts.next().unwrap_or_default();
    let channel = parts.next().unwrap_or_default();

    if adapter != expected_adapter || channel.is_empty() {
        return Err(ChatError::InvalidChannelId(channel_id.to_string()));
    }

    Ok(channel.to_string())
}

fn parse_slack_ts_millis(ts: &str) -> Option<i64> {
    let mut split = ts.splitn(2, '.');
    let seconds = split.next()?.parse::<i64>().ok()?;
    let fraction = split.next().unwrap_or("0");
    let mut millis_str = fraction.chars().take(3).collect::<String>();
    while millis_str.len() < 3 {
        millis_str.push('0');
    }
    let millis = millis_str.parse::<i64>().ok().unwrap_or(0);
    Some(seconds.saturating_mul(1000).saturating_add(millis))
}

fn parse_rfc3339_millis(value: &str) -> Option<i64> {
    OffsetDateTime::parse(value, &Rfc3339)
        .ok()
        .and_then(|dt| i64::try_from(dt.unix_timestamp_nanos() / 1_000_000).ok())
}

fn slack_reaction_name(emoji: &str) -> String {
    emoji.trim_matches(':').to_string()
}

fn now_millis() -> i64 {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    now.as_millis() as i64
}

fn derive_channel_id(thread_id: &str) -> String {
    let mut parts = thread_id.split(':');
    match (parts.next(), parts.next()) {
        (Some(adapter), Some(channel)) => format!("{adapter}:{channel}"),
        _ => thread_id.to_string(),
    }
}

fn contains_mention(text: &str, target: &str) -> bool {
    if target.is_empty() {
        return false;
    }

    let lower_text = text.to_lowercase();
    let lower_target = target.to_lowercase();
    let needle = format!("@{lower_target}");

    let mut search_start = 0;
    while let Some(found_at) = lower_text[search_start..].find(&needle) {
        let start = search_start + found_at;
        let end = start + needle.len();

        let boundary_ok = lower_text[end..]
            .chars()
            .next()
            .map(|ch| !ch.is_ascii_alphanumeric() && ch != '_' && ch != '-')
            .unwrap_or(true);

        if boundary_ok {
            return true;
        }

        search_start = start + 1;
    }

    false
}

fn merge_json(existing: Value, incoming: Value) -> Value {
    match (existing, incoming) {
        (Value::Object(mut left), Value::Object(right)) => {
            for (key, value) in right {
                left.insert(key, value);
            }
            Value::Object(left)
        }
        (_, incoming) => incoming,
    }
}

fn require_non_empty(value: String, field: &str) -> Result<String, ChatError> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err(ChatError::InvalidArgument(format!(
            "{field} must be a non-empty string",
        )));
    }
    Ok(trimmed.to_string())
}

fn validate_uuid_string(value: &str, field: &str) -> Result<(), ChatError> {
    Uuid::parse_str(value).map_err(|_| {
        ChatError::InvalidArgument(format!("{field} must be a valid UUID: '{value}'"))
    })?;
    Ok(())
}

fn require_json_object(value: &Value) -> Result<serde_json::Map<String, Value>, ChatError> {
    value
        .as_object()
        .cloned()
        .ok_or_else(|| ChatError::InvalidArgument("value must be a JSON object".to_string()))
}

#[pymodule]
fn _lsmsg_rs(_py: Python<'_>, module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<PyChat>()?;
    module.add_class::<PyInMemoryAdapter>()?;
    module.add_class::<PySlackAdapter>()?;
    module.add_class::<PyDiscordAdapter>()?;
    module.add_class::<PyLangGraphAdapter>()?;
    module.add_class::<PyThread>()?;
    module.add_class::<PyChannel>()?;
    module.add_class::<PyAuthor>()?;
    module.add_class::<PyMessage>()?;
    module.add_class::<PySentMessage>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Matcher;

    #[test]
    fn memory_state_subscription_roundtrip() {
        let state = MemoryState::default();
        assert!(!state.is_subscribed("thread-1"));
        state.subscribe("thread-1");
        assert!(state.is_subscribed("thread-1"));
        state.unsubscribe("thread-1");
        assert!(!state.is_subscribed("thread-1"));
    }

    #[test]
    fn memory_state_lock_exclusion() {
        let state = MemoryState::default();
        let lock_a = state.acquire_lock("thread-1", Duration::from_secs(30));
        assert!(lock_a.is_some());
        let lock_b = state.acquire_lock("thread-1", Duration::from_secs(30));
        assert!(lock_b.is_none());

        state.release_lock(&lock_a.unwrap());
        let lock_c = state.acquire_lock("thread-1", Duration::from_secs(30));
        assert!(lock_c.is_some());
    }

    #[test]
    fn in_memory_adapter_post_edit_delete() {
        let adapter = InMemoryAdapter::new("slack".to_string(), "bot".to_string(), None);

        let first = adapter
            .post_message("slack:C1:T1", &PostableMessage::Text("hello".to_string()))
            .expect("post should succeed");
        assert_eq!(first.thread_id, "slack:C1:T1");

        let second = adapter
            .post_message(
                "slack:C1:T1",
                &PostableMessage::Markdown("**world**".to_string()),
            )
            .expect("post should succeed");

        let messages = adapter
            .fetch_messages("slack:C1:T1", 50)
            .expect("fetch should succeed");
        assert_eq!(messages.len(), 2);

        adapter
            .edit_message(
                "slack:C1:T1",
                &second.id,
                &PostableMessage::Text("edited".to_string()),
            )
            .expect("edit should succeed");

        let edited = adapter
            .fetch_messages("slack:C1:T1", 50)
            .expect("fetch should succeed");
        assert_eq!(edited[1].text, "edited");
        assert!(edited[1].metadata.edited);

        adapter
            .delete_message("slack:C1:T1", &first.id)
            .expect("delete should succeed");
        let after_delete = adapter
            .fetch_messages("slack:C1:T1", 50)
            .expect("fetch should succeed");
        assert_eq!(after_delete.len(), 1);
    }

    #[test]
    fn contains_mention_handles_boundaries() {
        assert!(contains_mention("hi @bot", "bot"));
        assert!(contains_mention("hi @BOT!", "bot"));
        assert!(!contains_mention("hi @botman", "bot"));
        assert!(!contains_mention("hello bot", "bot"));
    }

    #[test]
    fn derive_channel_id_defaults_to_prefix_pair() {
        assert_eq!(derive_channel_id("slack:C123:111.222"), "slack:C123");
        assert_eq!(derive_channel_id("bad-id"), "bad-id");
    }

    #[test]
    fn parse_adapter_ids() {
        let (channel, thread) =
            parse_adapter_thread_id("slack", "slack:C123:111.222").expect("thread id should parse");
        assert_eq!(channel, "C123");
        assert_eq!(thread.as_deref(), Some("111.222"));

        let channel =
            parse_adapter_channel_id("discord", "discord:CH1").expect("channel id should parse");
        assert_eq!(channel, "CH1");
    }

    #[test]
    fn parse_timestamps() {
        assert_eq!(
            parse_slack_ts_millis("1710000000.123"),
            Some(1_710_000_000_123)
        );
        assert_eq!(
            parse_rfc3339_millis("2026-03-04T00:00:00Z"),
            Some(1_772_582_400_000)
        );
    }

    #[test]
    fn slack_adapter_http_roundtrip() {
        let mut server = mockito::Server::new();
        let api_base = server.url();

        let create_mock = server
            .mock("POST", "/chat.postMessage")
            .match_header("authorization", "Bearer xoxb-test")
            .match_body(Matcher::Regex(r#""channel":"C1""#.to_string()))
            .match_body(Matcher::Regex(r#""text":"reply""#.to_string()))
            .match_body(Matcher::Regex(
                r#""thread_ts":"1710000000\.100""#.to_string(),
            ))
            .with_status(200)
            .with_body(r#"{"ok":true,"channel":"C1","ts":"1710000000.200"}"#)
            .create();

        let edit_mock = server
            .mock("POST", "/chat.update")
            .match_header("authorization", "Bearer xoxb-test")
            .match_body(Matcher::Regex(r#""channel":"C1""#.to_string()))
            .match_body(Matcher::Regex(r#""ts":"1710000000\.200""#.to_string()))
            .with_status(200)
            .with_body(r#"{"ok":true}"#)
            .create();

        let fetch_mock = server
            .mock("GET", "/conversations.replies")
            .match_query(Matcher::AllOf(vec![
                Matcher::UrlEncoded("channel".into(), "C1".into()),
                Matcher::UrlEncoded("ts".into(), "1710000000.100".into()),
                Matcher::UrlEncoded("limit".into(), "20".into()),
            ]))
            .with_status(200)
            .with_body(
                r#"{"ok":true,"messages":[{"ts":"1710000000.100","thread_ts":"1710000000.100","text":"root","user":"U1"},{"ts":"1710000000.200","thread_ts":"1710000000.100","text":"reply","user":"U2"}]}"#,
            )
            .create();

        let delete_mock = server
            .mock("POST", "/chat.delete")
            .match_header("authorization", "Bearer xoxb-test")
            .match_body(Matcher::Regex(r#""channel":"C1""#.to_string()))
            .match_body(Matcher::Regex(r#""ts":"1710000000\.200""#.to_string()))
            .with_status(200)
            .with_body(r#"{"ok":true}"#)
            .create();

        let adapter = SlackAdapter::new(
            "xoxb-test".to_string(),
            "bot".to_string(),
            Some("U_BOT".to_string()),
            Some(api_base),
        )
        .expect("adapter should initialize");

        let sent = adapter
            .post_message(
                "slack:C1:1710000000.100",
                &PostableMessage::Text("reply".to_string()),
            )
            .expect("post should succeed");
        assert_eq!(sent.id, "1710000000.200");
        assert_eq!(sent.thread_id, "slack:C1:1710000000.100");

        adapter
            .edit_message(
                "slack:C1:1710000000.100",
                "1710000000.200",
                &PostableMessage::Text("reply edited".to_string()),
            )
            .expect("edit should succeed");

        let messages = adapter
            .fetch_messages("slack:C1:1710000000.100", 20)
            .expect("fetch should succeed");
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].text, "root");
        assert_eq!(messages[1].text, "reply");

        adapter
            .delete_message("slack:C1:1710000000.100", "1710000000.200")
            .expect("delete should succeed");

        create_mock.assert();
        edit_mock.assert();
        fetch_mock.assert();
        delete_mock.assert();
    }

    #[test]
    fn discord_adapter_http_roundtrip() {
        let mut server = mockito::Server::new();
        let api_base = server.url();

        let create_root_mock = server
            .mock("POST", "/channels/CHAN1/messages")
            .match_header("authorization", "Bot discord-token")
            .match_body(Matcher::Regex(r#""content":"root""#.to_string()))
            .with_status(200)
            .with_body(
                r#"{"id":"m1","channel_id":"CHAN1","content":"root","timestamp":"2026-03-04T00:00:00Z","author":{"id":"BOT","username":"bot","bot":true}}"#,
            )
            .create();

        let create_reply_mock = server
            .mock("POST", "/channels/CHAN1/messages")
            .match_header("authorization", "Bot discord-token")
            .match_body(Matcher::Regex(
                r#""message_reference":\{"message_id":"m1"\}"#.to_string(),
            ))
            .match_body(Matcher::Regex(r#""content":"reply""#.to_string()))
            .with_status(200)
            .with_body(
                r#"{"id":"m2","channel_id":"CHAN1","content":"reply","timestamp":"2026-03-04T00:00:01Z","author":{"id":"BOT","username":"bot","bot":true}}"#,
            )
            .create();

        let edit_mock = server
            .mock("PATCH", "/channels/CHAN1/messages/m2")
            .match_body(Matcher::Regex(r#""content":"reply edited""#.to_string()))
            .with_status(200)
            .with_body(r#"{"id":"m2"}"#)
            .create();

        let add_reaction_mock = server
            .mock(
                "PUT",
                "/channels/CHAN1/messages/m2/reactions/%F0%9F%94%A5/@me",
            )
            .with_status(204)
            .create();

        let remove_reaction_mock = server
            .mock(
                "DELETE",
                "/channels/CHAN1/messages/m2/reactions/%F0%9F%94%A5/@me",
            )
            .with_status(204)
            .create();

        let fetch_mock = server
            .mock("GET", "/channels/CHAN1/messages")
            .match_query(Matcher::UrlEncoded("limit".into(), "20".into()))
            .with_status(200)
            .with_body(
                r#"[{"id":"m2","channel_id":"CHAN1","content":"reply edited","timestamp":"2026-03-04T00:00:01Z","message_reference":{"message_id":"m1"},"author":{"id":"U2","username":"alice","bot":false}},{"id":"m1","channel_id":"CHAN1","content":"root","timestamp":"2026-03-04T00:00:00Z","author":{"id":"BOT","username":"bot","bot":true}}]"#,
            )
            .create();

        let delete_mock = server
            .mock("DELETE", "/channels/CHAN1/messages/m2")
            .with_status(204)
            .create();

        let adapter = DiscordAdapter::new(
            "discord-token".to_string(),
            "bot".to_string(),
            Some("BOT".to_string()),
            Some(api_base),
        )
        .expect("adapter should initialize");

        let root = adapter
            .post_channel_message("discord:CHAN1", &PostableMessage::Text("root".to_string()))
            .expect("root post should succeed");
        assert_eq!(root.id, "m1");
        assert_eq!(root.thread_id, "discord:CHAN1:m1");

        let sent = adapter
            .post_message(&root.thread_id, &PostableMessage::Text("reply".to_string()))
            .expect("reply post should succeed");
        assert_eq!(sent.id, "m2");
        assert_eq!(sent.thread_id, "discord:CHAN1:m1");

        adapter
            .edit_message(
                "discord:CHAN1:m1",
                "m2",
                &PostableMessage::Text("reply edited".to_string()),
            )
            .expect("edit should succeed");
        adapter
            .add_reaction("discord:CHAN1:m1", "m2", "🔥")
            .expect("add reaction should succeed");
        adapter
            .remove_reaction("discord:CHAN1:m1", "m2", "🔥")
            .expect("remove reaction should succeed");

        let messages = adapter
            .fetch_messages("discord:CHAN1:m1", 20)
            .expect("fetch should succeed");
        assert_eq!(messages.len(), 2);
        assert_eq!(messages[0].text, "root");
        assert_eq!(messages[1].text, "reply edited");

        adapter
            .delete_message("discord:CHAN1:m1", "m2")
            .expect("delete should succeed");

        create_root_mock.assert();
        create_reply_mock.assert();
        edit_mock.assert();
        add_reaction_mock.assert();
        remove_reaction_mock.assert();
        fetch_mock.assert();
        delete_mock.assert();
    }

    #[test]
    fn langgraph_adapter_uuid5_thread_mapping_is_stable() {
        let adapter = LangGraphAdapter::new(
            "https://example.com".to_string(),
            "assistant-a".to_string(),
            None,
            None,
        )
        .expect("adapter should initialize");

        let first = adapter
            .thread_id_for_external("slack", "T123", "C123", "1710000000.100")
            .expect("thread mapping should succeed");
        let second = adapter
            .thread_id_for_external("slack", "T123", "C123", "1710000000.100")
            .expect("thread mapping should succeed");
        let other = adapter
            .thread_id_for_external("slack", "T123", "C123", "1710000000.200")
            .expect("thread mapping should succeed");

        assert_eq!(first, second);
        assert_ne!(first, other);
        let parsed = Uuid::parse_str(&first).expect("mapped id should be a uuid");
        assert_eq!(parsed.get_version_num(), 5);
    }

    #[test]
    fn langgraph_adapter_trigger_external_run_roundtrip() {
        let mut server = mockito::Server::new();
        let api_base = server.url();

        let adapter = LangGraphAdapter::new(
            api_base,
            "assistant-a".to_string(),
            Some("sk-test".to_string()),
            None,
        )
        .expect("adapter should initialize");

        let thread_id = adapter
            .thread_id_for_external("slack", "T123", "C456", "1710000000.100")
            .expect("thread mapping should succeed");

        let thread_mock = server
            .mock("POST", "/threads")
            .match_header("authorization", "Bearer sk-test")
            .match_body(Matcher::Regex(r#""thread_id":"[0-9a-f-]{36}""#.to_string()))
            .match_body(Matcher::Regex(r#""if_exists":"do_nothing""#.to_string()))
            .match_body(Matcher::Regex(
                r#""chat_external_thread_key":"provider=slack\|workspace=T123\|channel=C456\|root=1710000000\.100""#
                    .to_string(),
            ))
            .with_status(200)
            .with_body(format!(
                r#"{{"thread_id":"{}","status":"idle","metadata":{{"ok":true}}}}"#,
                thread_id
            ))
            .create();

        let run_path = format!("/threads/{thread_id}/runs");
        let run_mock = server
            .mock("POST", run_path.as_str())
            .match_header("authorization", "Bearer sk-test")
            .match_body(Matcher::Regex(
                r#""assistant_id":"assistant-a""#.to_string(),
            ))
            .match_body(Matcher::Regex(r#""if_not_exists":"create""#.to_string()))
            .match_body(Matcher::Regex(
                r#""multitask_strategy":"enqueue""#.to_string(),
            ))
            .with_status(200)
            .with_body(format!(
                r#"{{"thread_id":"{}","run_id":"run_123","assistant_id":"assistant-a"}}"#,
                thread_id
            ))
            .create();

        let result = adapter
            .trigger_external_run(
                "slack",
                "T123",
                "C456",
                "1710000000.100",
                RunDispatchOptions {
                    input: Some(&json!({
                        "messages": [{"role":"user","content":"hello"}]
                    })),
                    thread_metadata: Some(&json!({"deployment":"dev"})),
                    run_metadata: Some(&json!({"event_id":"E1"})),
                    config: Some(&json!({"configurable":{"source":"slack"}})),
                    multitask_strategy: "enqueue",
                    if_not_exists: "create",
                    webhook: Some("https://example.com/hooks/1"),
                    durability: Some("async"),
                },
            )
            .expect("trigger run should succeed");

        assert_eq!(result["thread_id"].as_str(), Some(thread_id.as_str()));
        assert_eq!(result["run"]["run_id"].as_str(), Some("run_123"));
        assert_eq!(result["run"]["assistant_id"].as_str(), Some("assistant-a"));

        thread_mock.assert();
        run_mock.assert();
    }

    #[test]
    fn slack_adapter_surfaces_api_errors() {
        let mut server = mockito::Server::new();
        let api_base = server.url();
        let create_mock = server
            .mock("POST", "/chat.postMessage")
            .match_header("authorization", "Bearer xoxb-test")
            .with_status(200)
            .with_body(r#"{"ok":false,"error":"channel_not_found"}"#)
            .create();

        let adapter = SlackAdapter::new(
            "xoxb-test".to_string(),
            "bot".to_string(),
            Some("U_BOT".to_string()),
            Some(api_base),
        )
        .expect("adapter should initialize");

        let result =
            adapter.post_channel_message("slack:C1", &PostableMessage::Text("hello".to_string()));
        let err = result.expect_err("expected adapter error");
        assert!(err.to_string().contains("channel_not_found"));
        create_mock.assert();
    }

    #[test]
    fn discord_adapter_surfaces_http_errors() {
        let mut server = mockito::Server::new();
        let api_base = server.url();
        let create_mock = server
            .mock("POST", "/channels/CHAN1/messages")
            .with_status(400)
            .with_body(r#"{"message":"bad request"}"#)
            .create();

        let adapter = DiscordAdapter::new(
            "discord-token".to_string(),
            "bot".to_string(),
            Some("BOT".to_string()),
            Some(api_base),
        )
        .expect("adapter should initialize");

        let result = adapter
            .post_channel_message("discord:CHAN1", &PostableMessage::Text("hello".to_string()));
        let err = result.expect_err("expected adapter error");
        assert!(err.to_string().contains("status 400"));
        create_mock.assert();
    }

    #[test]
    fn langgraph_adapter_reports_invalid_json_response() {
        let mut server = mockito::Server::new();
        let api_base = server.url();
        let adapter = LangGraphAdapter::new(
            api_base,
            "assistant-a".to_string(),
            Some("sk-test".to_string()),
            None,
        )
        .expect("adapter should initialize");

        let thread_id = adapter
            .thread_id_for_external("slack", "T123", "C456", "1710000000.100")
            .expect("thread mapping should succeed");
        let thread_mock = server
            .mock("POST", "/threads")
            .with_status(200)
            .with_body(r#"{"thread_id":"ok"}"#)
            .create();
        let run_mock = server
            .mock("POST", format!("/threads/{thread_id}/runs").as_str())
            .with_status(200)
            .with_body("{not json")
            .create();

        let result = adapter.trigger_external_run(
            "slack",
            "T123",
            "C456",
            "1710000000.100",
            RunDispatchOptions {
                input: Some(&json!({"messages": [{"role":"user","content":"hello"}]})),
                thread_metadata: None,
                run_metadata: None,
                config: None,
                multitask_strategy: "enqueue",
                if_not_exists: "create",
                webhook: None,
                durability: None,
            },
        );
        let err = result.expect_err("expected parse error");
        assert!(err.to_string().contains("invalid json body"));
        thread_mock.assert();
        run_mock.assert();
    }
}
