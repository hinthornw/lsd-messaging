use std::io::BufRead;

use reqwest::blocking::Client;
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::error::{LsmsgError, Result};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunResult {
    pub id: String,
    pub status: String,
    pub output: Value,
}

impl RunResult {
    /// Extract the text content of the last message in the output.
    pub fn text(&self) -> String {
        let messages = self.output.get("messages").and_then(|v| v.as_array());
        if let Some(msgs) = messages {
            if let Some(last) = msgs.last() {
                if let Some(content) = last.get("content").and_then(|v| v.as_str()) {
                    return content.to_string();
                }
            }
        }
        String::new()
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RunChunk {
    pub event: String,
    pub text: String,
    pub text_delta: String,
    pub data: Value,
}

#[derive(Clone, Debug)]
pub struct CreateRunParams {
    pub agent: String,
    pub thread_id: String,
    pub input: Option<Value>,
    pub config: Option<Value>,
    pub metadata: Option<Value>,
}

/// Blocking HTTP client for the LangGraph API.
pub struct LangGraphClient {
    client: Client,
    base_url: String,
    api_key: Option<String>,
}

impl LangGraphClient {
    pub fn new(base_url: &str, api_key: Option<&str>) -> Self {
        Self {
            client: Client::new(),
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key: api_key.map(String::from),
        }
    }

    /// Create a new run and return its ID.
    pub fn create_run(&self, params: &CreateRunParams) -> Result<String> {
        let url = format!("{}/threads/{}/runs", self.base_url, params.thread_id);

        let mut body = json!({
            "assistant_id": params.agent,
            "if_not_exists": "create",
        });

        if let Some(ref input) = params.input {
            body["input"] = input.clone();
        }
        if let Some(ref config) = params.config {
            body["config"] = config.clone();
        }
        if let Some(ref metadata) = params.metadata {
            body["metadata"] = metadata.clone();
        }

        let resp = self
            .client
            .post(&url)
            .headers(self.default_headers())
            .json(&body)
            .send()?;

        let status = resp.status().as_u16();
        if !resp.status().is_success() {
            let text = resp.text().unwrap_or_default();
            return Err(LsmsgError::Api { status, body: text });
        }

        let data: Value = resp.json()?;
        let run_id = data
            .get("run_id")
            .and_then(|v| v.as_str())
            .ok_or_else(|| LsmsgError::InvalidPayload("missing run_id in response".into()))?
            .to_string();

        Ok(run_id)
    }

    /// Block until a run completes and return its result.
    pub fn wait_run(&self, thread_id: &str, run_id: &str) -> Result<RunResult> {
        let url = format!(
            "{}/threads/{}/runs/{}/join",
            self.base_url, thread_id, run_id
        );

        let resp = self
            .client
            .get(&url)
            .headers(self.default_headers())
            .send()?;

        let status = resp.status().as_u16();
        if !resp.status().is_success() {
            let text = resp.text().unwrap_or_default();
            return Err(LsmsgError::Api { status, body: text });
        }

        let output: Value = resp.json()?;
        Ok(RunResult {
            id: run_id.to_string(),
            status: "completed".to_string(),
            output,
        })
    }

    /// Create a run and stream results as SSE, calling `on_chunk` for each chunk.
    /// Returns when the stream ends.
    pub fn stream_new_run(
        &self,
        params: &CreateRunParams,
        on_chunk: &mut dyn FnMut(RunChunk),
    ) -> Result<()> {
        let url = format!("{}/threads/{}/runs/stream", self.base_url, params.thread_id);

        let mut body = json!({
            "assistant_id": params.agent,
            "stream_mode": "messages",
            "if_not_exists": "create",
        });

        if let Some(ref input) = params.input {
            body["input"] = input.clone();
        }
        if let Some(ref config) = params.config {
            body["config"] = config.clone();
        }
        if let Some(ref metadata) = params.metadata {
            body["metadata"] = metadata.clone();
        }

        let resp = self
            .client
            .post(&url)
            .headers(self.default_headers())
            .json(&body)
            .send()?;

        let status = resp.status().as_u16();
        if !resp.status().is_success() {
            let text = resp.text().unwrap_or_default();
            return Err(LsmsgError::Api { status, body: text });
        }

        let reader = std::io::BufReader::new(resp);
        let mut accumulated = String::new();
        let mut current_event = String::new();

        for line in reader.lines() {
            let line = line.map_err(|e| LsmsgError::Http(e.to_string()))?;

            if let Some(event_name) = line.strip_prefix("event: ") {
                current_event = event_name.to_string();
                continue;
            }

            if let Some(data_str) = line.strip_prefix("data: ") {
                let data: Value = serde_json::from_str(data_str).unwrap_or(Value::Null);
                let mut text_delta = String::new();

                if current_event == "messages/partial" {
                    if let Some(arr) = data.as_array() {
                        if let Some(last) = arr.last() {
                            text_delta = last
                                .get("content")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string();
                        }
                    } else if let Some(content) = data.get("content").and_then(|v| v.as_str()) {
                        text_delta = content.to_string();
                    }
                }

                accumulated.push_str(&text_delta);
                on_chunk(RunChunk {
                    event: current_event.clone(),
                    text: accumulated.clone(),
                    text_delta,
                    data: if data.is_object() {
                        data
                    } else {
                        Value::Object(serde_json::Map::new())
                    },
                });
                continue;
            }

            // Empty line = end of SSE event (reset for next)
        }

        Ok(())
    }

    /// Collect all chunks from a streamed run into a Vec.
    pub fn stream_new_run_collect(&self, params: &CreateRunParams) -> Result<Vec<RunChunk>> {
        let mut chunks = Vec::new();
        self.stream_new_run(params, &mut |chunk| {
            chunks.push(chunk);
        })?;
        Ok(chunks)
    }

    /// Cancel a running run.
    pub fn cancel_run(&self, thread_id: &str, run_id: &str) -> Result<()> {
        let url = format!(
            "{}/threads/{}/runs/{}/cancel",
            self.base_url, thread_id, run_id
        );

        let resp = self
            .client
            .post(&url)
            .headers(self.default_headers())
            .send()?;

        if !resp.status().is_success() {
            let status = resp.status().as_u16();
            let text = resp.text().unwrap_or_default();
            return Err(LsmsgError::Api { status, body: text });
        }

        Ok(())
    }

    fn default_headers(&self) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        if let Some(ref key) = self.api_key {
            if let Ok(val) = HeaderValue::from_str(&format!("Bearer {key}")) {
                headers.insert(AUTHORIZATION, val);
            }
        }
        headers
    }
}
