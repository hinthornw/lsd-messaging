use std::collections::HashMap;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use serde_json::Value;

use lsmsg_core::{
    CreateRunParams, Event, EventKind, HandlerRegistry, LangGraphClient, Platform,
    PlatformCapabilities, RunChunk, RunResult,
};

// ---------------------------------------------------------------------------
// Helpers: serde_json::Value <-> Python
// ---------------------------------------------------------------------------

fn value_to_py(py: Python<'_>, val: &Value) -> PyObject {
    match val {
        Value::Null => py.None(),
        Value::Bool(b) => {
            let val: bool = *b;
            val.into_pyobject(py)
                .unwrap()
                .to_owned()
                .into_any()
                .unbind()
        }
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_pyobject(py).unwrap().into_any().unbind()
            } else if let Some(f) = n.as_f64() {
                f.into_pyobject(py).unwrap().into_any().unbind()
            } else {
                py.None()
            }
        }
        Value::String(s) => s.into_pyobject(py).unwrap().into_any().unbind(),
        Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(value_to_py(py, item)).unwrap();
            }
            list.into_pyobject(py).unwrap().into_any().unbind()
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, value_to_py(py, v)).unwrap();
            }
            dict.into_pyobject(py).unwrap().into_any().unbind()
        }
    }
}

fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    // Use Python's json.dumps for reliable conversion
    let json_mod = obj.py().import("json")?;
    let dumped: String = json_mod.call_method1("dumps", (obj,))?.extract()?;
    serde_json::from_str(&dumped)
        .map_err(|e| PyValueError::new_err(format!("failed to convert Python object to JSON: {e}")))
}

fn optional_py_to_value(obj: Option<&Bound<'_, PyAny>>) -> PyResult<Option<Value>> {
    match obj {
        None => Ok(None),
        Some(o) if o.is_none() => Ok(None),
        Some(o) => Ok(Some(py_to_value(o)?)),
    }
}

// ---------------------------------------------------------------------------
// Event → Python dict
// ---------------------------------------------------------------------------

fn event_to_py(py: Python<'_>, event: &Event) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("kind", event.kind.as_str())?;
    dict.set_item("platform", platform_caps_to_py(py, &event.platform)?)?;
    dict.set_item("workspace_id", &event.workspace_id)?;
    dict.set_item("channel_id", &event.channel_id)?;
    dict.set_item("thread_id", &event.thread_id)?;
    dict.set_item("message_id", &event.message_id)?;
    dict.set_item("internal_thread_id", event.internal_thread_id())?;

    let user_dict = PyDict::new(py);
    user_dict.set_item("id", &event.user.id)?;
    user_dict.set_item("name", event.user.name.as_deref())?;
    user_dict.set_item("email", event.user.email.as_deref())?;
    dict.set_item("user", user_dict)?;

    dict.set_item("text", &event.text)?;
    dict.set_item("command", event.command.as_deref())?;
    dict.set_item("emoji", event.emoji.as_deref())?;
    dict.set_item("raw_event_type", event.raw_event_type.as_deref())?;
    dict.set_item("raw", value_to_py(py, &event.raw))?;

    Ok(dict.into_pyobject(py).unwrap().into_any().unbind())
}

fn platform_caps_to_py(py: Python<'_>, caps: &PlatformCapabilities) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("name", caps.name.as_str())?;
    dict.set_item("ephemeral", caps.ephemeral)?;
    dict.set_item("threads", caps.threads)?;
    dict.set_item("reactions", caps.reactions)?;
    dict.set_item("streaming", caps.streaming)?;
    dict.set_item("modals", caps.modals)?;
    dict.set_item("typing_indicator", caps.typing_indicator)?;
    Ok(dict.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// PySlackParser
// ---------------------------------------------------------------------------

#[pyclass]
struct SlackParser;

#[pymethods]
impl SlackParser {
    #[new]
    fn new() -> Self {
        Self
    }

    /// Verify a Slack request signature.
    #[staticmethod]
    fn verify_signature(
        signing_secret: &str,
        timestamp: &str,
        signature: &str,
        body: &[u8],
    ) -> bool {
        lsmsg_core::slack::verify_signature(signing_secret, timestamp, signature, body)
    }

    /// Parse a Slack webhook body. Returns a dict with key "type":
    /// - {"type": "event", "event": {...}} for events
    /// - {"type": "challenge", "challenge": "..."} for url_verification
    /// - {"type": "ignored"} for ignored payloads
    #[staticmethod]
    fn parse_webhook(py: Python<'_>, body: &[u8], content_type: &str) -> PyResult<PyObject> {
        let headers = HashMap::new();
        let result = lsmsg_core::slack::parse_webhook(body, content_type, &headers);
        let dict = PyDict::new(py);
        match result {
            lsmsg_core::slack::SlackWebhookResult::Event(event) => {
                dict.set_item("type", "event")?;
                dict.set_item("event", event_to_py(py, &event)?)?;
            }
            lsmsg_core::slack::SlackWebhookResult::Challenge(c) => {
                dict.set_item("type", "challenge")?;
                dict.set_item("challenge", c)?;
            }
            lsmsg_core::slack::SlackWebhookResult::Ignored => {
                dict.set_item("type", "ignored")?;
            }
        }
        Ok(dict.into_pyobject(py).unwrap().into_any().unbind())
    }

    /// Strip Slack mentions from text.
    #[staticmethod]
    fn strip_mentions(text: &str) -> String {
        lsmsg_core::slack::strip_mentions(text)
    }
}

// ---------------------------------------------------------------------------
// PyTeamsParser
// ---------------------------------------------------------------------------

#[pyclass]
struct TeamsParser;

#[pymethods]
impl TeamsParser {
    #[new]
    fn new() -> Self {
        Self
    }

    /// Parse a Teams webhook payload (as a Python dict). Returns event dict or None.
    #[staticmethod]
    fn parse_webhook(py: Python<'_>, payload: &Bound<'_, PyAny>) -> PyResult<PyObject> {
        let value = py_to_value(payload)?;
        match lsmsg_core::teams::parse_webhook(&value) {
            Some(event) => event_to_py(py, &event),
            None => Ok(py.None()),
        }
    }

    /// Strip Teams mentions from text.
    #[staticmethod]
    fn strip_mentions(text: &str) -> String {
        lsmsg_core::teams::strip_mentions(text)
    }
}

// ---------------------------------------------------------------------------
// PyHandlerRegistry
// ---------------------------------------------------------------------------

#[pyclass]
struct PyHandlerRegistry {
    inner: HandlerRegistry,
}

#[pymethods]
impl PyHandlerRegistry {
    #[new]
    fn new() -> Self {
        Self {
            inner: HandlerRegistry::new(),
        }
    }

    /// Register a handler filter. Returns the handler ID.
    #[pyo3(signature = (event_kind=None, command=None, pattern=None, emoji=None, platform=None, raw_event_type=None))]
    fn register(
        &mut self,
        event_kind: Option<&str>,
        command: Option<String>,
        pattern: Option<&str>,
        emoji: Option<String>,
        platform: Option<&str>,
        raw_event_type: Option<String>,
    ) -> PyResult<u64> {
        let ek = event_kind.map(str_to_event_kind).transpose()?;
        let plat = platform.map(str_to_platform).transpose()?;
        self.inner
            .register_from_fields(ek, command, pattern, emoji, plat, raw_event_type)
            .map_err(|e| PyValueError::new_err(e.to_string()))
    }

    fn unregister(&mut self, id: u64) -> bool {
        self.inner.unregister(id)
    }

    /// Return list of handler IDs matching the event dict.
    fn match_event(&self, _py: Python<'_>, event_dict: &Bound<'_, PyAny>) -> PyResult<Vec<u64>> {
        let value = py_to_value(event_dict)?;
        let event: Event = serde_json::from_value(value)
            .map_err(|e| PyValueError::new_err(format!("invalid event: {e}")))?;
        Ok(self.inner.match_event(&event))
    }
}

fn str_to_event_kind(s: &str) -> PyResult<EventKind> {
    match s {
        "message" => Ok(EventKind::Message),
        "mention" => Ok(EventKind::Mention),
        "command" => Ok(EventKind::Command),
        "reaction" => Ok(EventKind::Reaction),
        "raw" => Ok(EventKind::Raw),
        _ => Err(PyValueError::new_err(format!("unknown event kind: {s}"))),
    }
}

fn str_to_platform(s: &str) -> PyResult<Platform> {
    match s {
        "slack" => Ok(Platform::Slack),
        "teams" => Ok(Platform::Teams),
        "discord" => Ok(Platform::Discord),
        "telegram" => Ok(Platform::Telegram),
        "github" => Ok(Platform::Github),
        "linear" => Ok(Platform::Linear),
        "gchat" => Ok(Platform::Gchat),
        _ => Err(PyValueError::new_err(format!("unknown platform: {s}"))),
    }
}

// ---------------------------------------------------------------------------
// PyLangGraphClient
// ---------------------------------------------------------------------------

#[pyclass]
struct PyLangGraphClient {
    inner: LangGraphClient,
}

#[pymethods]
impl PyLangGraphClient {
    #[new]
    #[pyo3(signature = (base_url, api_key=None))]
    fn new(base_url: &str, api_key: Option<&str>) -> Self {
        Self {
            inner: LangGraphClient::new(base_url, api_key),
        }
    }

    /// Create a run. Returns the run ID string.
    #[pyo3(signature = (agent, thread_id, input=None, config=None, metadata=None))]
    fn create_run(
        &self,
        agent: &str,
        thread_id: &str,
        input: Option<&Bound<'_, PyAny>>,
        config: Option<&Bound<'_, PyAny>>,
        metadata: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<String> {
        let params = CreateRunParams {
            agent: agent.to_string(),
            thread_id: thread_id.to_string(),
            input: optional_py_to_value(input)?,
            config: optional_py_to_value(config)?,
            metadata: optional_py_to_value(metadata)?,
        };
        self.inner
            .create_run(&params)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

    /// Wait for a run to complete. Returns a dict with id, status, output.
    fn wait_run(&self, py: Python<'_>, thread_id: &str, run_id: &str) -> PyResult<PyObject> {
        let result = self
            .inner
            .wait_run(thread_id, run_id)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        run_result_to_py(py, &result)
    }

    /// Stream a new run, collecting all chunks. Returns list of chunk dicts.
    #[pyo3(signature = (agent, thread_id, input=None, config=None, metadata=None))]
    fn stream_new_run(
        &self,
        py: Python<'_>,
        agent: &str,
        thread_id: &str,
        input: Option<&Bound<'_, PyAny>>,
        config: Option<&Bound<'_, PyAny>>,
        metadata: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<PyObject> {
        let params = CreateRunParams {
            agent: agent.to_string(),
            thread_id: thread_id.to_string(),
            input: optional_py_to_value(input)?,
            config: optional_py_to_value(config)?,
            metadata: optional_py_to_value(metadata)?,
        };
        let chunks = self
            .inner
            .stream_new_run_collect(&params)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;

        let list = PyList::empty(py);
        for chunk in &chunks {
            list.append(run_chunk_to_py(py, chunk)?)?;
        }
        Ok(list.into_pyobject(py).unwrap().into_any().unbind())
    }

    fn cancel_run(&self, thread_id: &str, run_id: &str) -> PyResult<()> {
        self.inner
            .cancel_run(thread_id, run_id)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }
}

fn run_result_to_py(py: Python<'_>, result: &RunResult) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("id", &result.id)?;
    dict.set_item("status", &result.status)?;
    dict.set_item("output", value_to_py(py, &result.output))?;
    dict.set_item("text", result.text())?;
    Ok(dict.into_pyobject(py).unwrap().into_any().unbind())
}

fn run_chunk_to_py(py: Python<'_>, chunk: &RunChunk) -> PyResult<PyObject> {
    let dict = PyDict::new(py);
    dict.set_item("event", &chunk.event)?;
    dict.set_item("text", &chunk.text)?;
    dict.set_item("text_delta", &chunk.text_delta)?;
    dict.set_item("data", value_to_py(py, &chunk.data))?;
    Ok(dict.into_pyobject(py).unwrap().into_any().unbind())
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

#[pyfunction]
fn deterministic_thread_id(
    platform: &str,
    workspace_id: &str,
    channel_id: &str,
    thread_id: &str,
) -> PyResult<String> {
    let plat = str_to_platform(platform)?;
    Ok(lsmsg_core::deterministic_thread_id(
        &plat,
        workspace_id,
        channel_id,
        thread_id,
    ))
}

#[pyfunction]
fn platform_capabilities(platform: &str) -> PyResult<PyObject> {
    let plat = str_to_platform(platform)?;
    let caps = lsmsg_core::platform::capabilities_for(&plat);
    Python::with_gil(|py| platform_caps_to_py(py, &caps))
}

// ---------------------------------------------------------------------------
// Module
// ---------------------------------------------------------------------------

#[pymodule]
fn _lsmsg_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SlackParser>()?;
    m.add_class::<TeamsParser>()?;
    m.add_class::<PyHandlerRegistry>()?;
    m.add_class::<PyLangGraphClient>()?;
    m.add_function(wrap_pyfunction!(deterministic_thread_id, m)?)?;
    m.add_function(wrap_pyfunction!(platform_capabilities, m)?)?;
    Ok(())
}
