use std::collections::HashMap;

use napi::bindgen_prelude::*;
use napi_derive::napi;
use serde_json::Value;

// ---------------------------------------------------------------------------
// Slack
// ---------------------------------------------------------------------------

#[napi]
pub fn slack_verify_signature(
    signing_secret: String,
    timestamp: String,
    signature: String,
    body: Buffer,
) -> bool {
    lsmsg_core::slack::verify_signature(&signing_secret, &timestamp, &signature, &body)
}

/// Parse a Slack webhook. Returns a JSON object with { type, event?, challenge? }.
#[napi]
pub fn slack_parse_webhook(body: Buffer, content_type: String) -> serde_json::Value {
    let headers = HashMap::new();
    let result = lsmsg_core::slack::parse_webhook(&body, &content_type, &headers);
    match result {
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
    }
}

#[napi]
pub fn slack_strip_mentions(text: String) -> String {
    lsmsg_core::slack::strip_mentions(&text)
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

/// Parse a Teams webhook. Returns event JSON or null.
#[napi]
pub fn teams_parse_webhook(payload: serde_json::Value) -> Option<serde_json::Value> {
    lsmsg_core::teams::parse_webhook(&payload)
        .map(|event| serde_json::to_value(&event).unwrap_or(Value::Null))
}

#[napi]
pub fn teams_strip_mentions(text: String) -> String {
    lsmsg_core::teams::strip_mentions(&text)
}

// ---------------------------------------------------------------------------
// Handler Registry
// ---------------------------------------------------------------------------

#[napi]
pub struct HandlerRegistry {
    inner: lsmsg_core::HandlerRegistry,
}

#[napi]
impl HandlerRegistry {
    #[napi(constructor)]
    pub fn new() -> Self {
        Self {
            inner: lsmsg_core::HandlerRegistry::new(),
        }
    }

    #[napi]
    pub fn register(
        &mut self,
        event_kind: Option<String>,
        command: Option<String>,
        pattern: Option<String>,
        emoji: Option<String>,
        platform: Option<String>,
        raw_event_type: Option<String>,
    ) -> Result<i64> {
        let ek = event_kind.as_deref().map(str_to_event_kind).transpose()?;
        let plat = platform.as_deref().map(str_to_platform).transpose()?;
        let id = self
            .inner
            .register_from_fields(ek, command, pattern.as_deref(), emoji, plat, raw_event_type)
            .map_err(|e| Error::from_reason(e.to_string()))?;
        Ok(id as i64)
    }

    #[napi]
    pub fn unregister(&mut self, id: i64) -> bool {
        self.inner.unregister(id as u64)
    }

    #[napi]
    pub fn match_event(&self, event_json: serde_json::Value) -> Result<Vec<i64>> {
        let event: lsmsg_core::Event = serde_json::from_value(event_json)
            .map_err(|e| Error::from_reason(format!("invalid event: {e}")))?;
        Ok(self
            .inner
            .match_event(&event)
            .into_iter()
            .map(|id| id as i64)
            .collect())
    }
}

// ---------------------------------------------------------------------------
// LangGraph Client
// ---------------------------------------------------------------------------

#[napi]
pub struct LangGraphClient {
    inner: lsmsg_core::LangGraphClient,
}

#[napi]
impl LangGraphClient {
    #[napi(constructor)]
    pub fn new(base_url: String, api_key: Option<String>) -> Self {
        Self {
            inner: lsmsg_core::LangGraphClient::new(&base_url, api_key.as_deref()),
        }
    }

    #[napi]
    pub fn create_run(
        &self,
        agent: String,
        thread_id: String,
        input: Option<serde_json::Value>,
        config: Option<serde_json::Value>,
        metadata: Option<serde_json::Value>,
    ) -> Result<String> {
        let params = lsmsg_core::CreateRunParams {
            agent,
            thread_id,
            input,
            config,
            metadata,
        };
        self.inner
            .create_run(&params)
            .map_err(|e| Error::from_reason(e.to_string()))
    }

    #[napi]
    pub fn wait_run(&self, thread_id: String, run_id: String) -> Result<serde_json::Value> {
        let result = self
            .inner
            .wait_run(&thread_id, &run_id)
            .map_err(|e| Error::from_reason(e.to_string()))?;
        serde_json::to_value(&result).map_err(|e| Error::from_reason(e.to_string()))
    }

    #[napi]
    pub fn stream_new_run(
        &self,
        agent: String,
        thread_id: String,
        input: Option<serde_json::Value>,
        config: Option<serde_json::Value>,
        metadata: Option<serde_json::Value>,
    ) -> Result<Vec<serde_json::Value>> {
        let params = lsmsg_core::CreateRunParams {
            agent,
            thread_id,
            input,
            config,
            metadata,
        };
        let chunks = self
            .inner
            .stream_new_run_collect(&params)
            .map_err(|e| Error::from_reason(e.to_string()))?;
        chunks
            .iter()
            .map(|c| serde_json::to_value(c).map_err(|e| Error::from_reason(e.to_string())))
            .collect()
    }

    #[napi]
    pub fn cancel_run(&self, thread_id: String, run_id: String) -> Result<()> {
        self.inner
            .cancel_run(&thread_id, &run_id)
            .map_err(|e| Error::from_reason(e.to_string()))
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

#[napi]
pub fn deterministic_thread_id(
    platform: String,
    workspace_id: String,
    channel_id: String,
    thread_id: String,
) -> Result<String> {
    let plat = str_to_platform(&platform)?;
    Ok(lsmsg_core::deterministic_thread_id(
        &plat,
        &workspace_id,
        &channel_id,
        &thread_id,
    ))
}

#[napi]
pub fn platform_capabilities(platform: String) -> Result<serde_json::Value> {
    let plat = str_to_platform(&platform)?;
    let caps = lsmsg_core::platform::capabilities_for(&plat);
    serde_json::to_value(&caps).map_err(|e| Error::from_reason(e.to_string()))
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn str_to_event_kind(s: &str) -> Result<lsmsg_core::EventKind> {
    match s {
        "message" => Ok(lsmsg_core::EventKind::Message),
        "mention" => Ok(lsmsg_core::EventKind::Mention),
        "command" => Ok(lsmsg_core::EventKind::Command),
        "reaction" => Ok(lsmsg_core::EventKind::Reaction),
        "raw" => Ok(lsmsg_core::EventKind::Raw),
        _ => Err(Error::from_reason(format!("unknown event kind: {s}"))),
    }
}

fn str_to_platform(s: &str) -> Result<lsmsg_core::Platform> {
    match s {
        "slack" => Ok(lsmsg_core::Platform::Slack),
        "teams" => Ok(lsmsg_core::Platform::Teams),
        "discord" => Ok(lsmsg_core::Platform::Discord),
        "telegram" => Ok(lsmsg_core::Platform::Telegram),
        "github" => Ok(lsmsg_core::Platform::Github),
        "linear" => Ok(lsmsg_core::Platform::Linear),
        "gchat" => Ok(lsmsg_core::Platform::Gchat),
        _ => Err(Error::from_reason(format!("unknown platform: {s}"))),
    }
}
