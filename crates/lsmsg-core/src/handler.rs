use regex::Regex;

use crate::error::{LsmsgError, Result};
use crate::event::{Event, EventKind};
use crate::platform::Platform;

/// Filter criteria for matching events to handlers.
pub struct HandlerFilter {
    pub event_kind: Option<EventKind>,
    pub command: Option<String>,
    pub pattern: Option<Regex>,
    pub emoji: Option<String>,
    pub platform: Option<Platform>,
    pub raw_event_type: Option<String>,
}

/// Check whether an event matches a handler's filter.
pub fn matches(filter: &HandlerFilter, event: &Event) -> bool {
    if let Some(ref kind) = filter.event_kind {
        if &event.kind != kind {
            return false;
        }
    }

    if let Some(ref platform) = filter.platform {
        if &event.platform.name != platform {
            return false;
        }
    }

    if let Some(ref command) = filter.command {
        match &event.command {
            Some(c) if c == command => {}
            _ => return false,
        }
    }

    if let Some(ref pattern) = filter.pattern {
        if !pattern.is_match(&event.text) {
            return false;
        }
    }

    if let Some(ref emoji) = filter.emoji {
        match &event.emoji {
            Some(e) if e == emoji => {}
            _ => return false,
        }
    }

    if let Some(ref raw_type) = filter.raw_event_type {
        match &event.raw_event_type {
            Some(t) if t == raw_type => {}
            _ => return false,
        }
    }

    true
}

/// Registry mapping opaque handler IDs to filters.
/// Each SDK maintains its own ID-to-callback mapping.
pub struct HandlerRegistry {
    handlers: Vec<(u64, HandlerFilter)>,
    next_id: u64,
}

impl HandlerRegistry {
    pub fn new() -> Self {
        Self {
            handlers: Vec::new(),
            next_id: 1,
        }
    }

    /// Register a filter and return its unique ID.
    pub fn register(&mut self, filter: HandlerFilter) -> u64 {
        let id = self.next_id;
        self.next_id += 1;
        self.handlers.push((id, filter));
        id
    }

    /// Register a filter built from individual fields.
    /// `pattern` is compiled to a regex if provided.
    pub fn register_from_fields(
        &mut self,
        event_kind: Option<EventKind>,
        command: Option<String>,
        pattern: Option<&str>,
        emoji: Option<String>,
        platform: Option<Platform>,
        raw_event_type: Option<String>,
    ) -> Result<u64> {
        let compiled_pattern = match pattern {
            Some(p) => Some(Regex::new(p).map_err(|e| LsmsgError::InvalidPattern(e.to_string()))?),
            None => None,
        };

        Ok(self.register(HandlerFilter {
            event_kind,
            command,
            pattern: compiled_pattern,
            emoji,
            platform,
            raw_event_type,
        }))
    }

    /// Remove a handler by ID. Returns true if found.
    pub fn unregister(&mut self, id: u64) -> bool {
        let len_before = self.handlers.len();
        self.handlers.retain(|(hid, _)| *hid != id);
        self.handlers.len() < len_before
    }

    /// Return the IDs of all handlers matching the given event.
    pub fn match_event(&self, event: &Event) -> Vec<u64> {
        self.handlers
            .iter()
            .filter(|(_, filter)| matches(filter, event))
            .map(|(id, _)| *id)
            .collect()
    }
}

impl Default for HandlerRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::UserInfo;
    use crate::platform;

    fn test_event(kind: EventKind) -> Event {
        Event {
            kind,
            platform: platform::slack(),
            workspace_id: "T1".into(),
            channel_id: "C1".into(),
            thread_id: "t1".into(),
            message_id: "m1".into(),
            user: UserInfo {
                id: "U1".into(),
                name: None,
                email: None,
            },
            text: "hello world".into(),
            command: None,
            emoji: None,
            raw_event_type: None,
            raw: serde_json::Value::Null,
        }
    }

    #[test]
    fn test_match_by_kind() {
        let filter = HandlerFilter {
            event_kind: Some(EventKind::Mention),
            command: None,
            pattern: None,
            emoji: None,
            platform: None,
            raw_event_type: None,
        };
        assert!(matches(&filter, &test_event(EventKind::Mention)));
        assert!(!matches(&filter, &test_event(EventKind::Message)));
    }

    #[test]
    fn test_match_by_pattern() {
        let filter = HandlerFilter {
            event_kind: None,
            command: None,
            pattern: Some(Regex::new("hello").unwrap()),
            emoji: None,
            platform: None,
            raw_event_type: None,
        };
        assert!(matches(&filter, &test_event(EventKind::Message)));

        let filter_no_match = HandlerFilter {
            event_kind: None,
            command: None,
            pattern: Some(Regex::new("xyz").unwrap()),
            emoji: None,
            platform: None,
            raw_event_type: None,
        };
        assert!(!matches(&filter_no_match, &test_event(EventKind::Message)));
    }

    #[test]
    fn test_match_by_command() {
        let mut event = test_event(EventKind::Command);
        event.command = Some("/echo".into());

        let filter = HandlerFilter {
            event_kind: Some(EventKind::Command),
            command: Some("/echo".into()),
            pattern: None,
            emoji: None,
            platform: None,
            raw_event_type: None,
        };
        assert!(matches(&filter, &event));
    }

    #[test]
    fn test_match_by_emoji() {
        let mut event = test_event(EventKind::Reaction);
        event.emoji = Some("thumbsup".into());

        let filter = HandlerFilter {
            event_kind: Some(EventKind::Reaction),
            command: None,
            pattern: None,
            emoji: Some("thumbsup".into()),
            platform: None,
            raw_event_type: None,
        };
        assert!(matches(&filter, &event));
    }

    #[test]
    fn test_match_by_platform() {
        let filter = HandlerFilter {
            event_kind: None,
            command: None,
            pattern: None,
            emoji: None,
            platform: Some(Platform::Slack),
            raw_event_type: None,
        };
        assert!(matches(&filter, &test_event(EventKind::Message)));

        let filter_teams = HandlerFilter {
            event_kind: None,
            command: None,
            pattern: None,
            emoji: None,
            platform: Some(Platform::Teams),
            raw_event_type: None,
        };
        assert!(!matches(&filter_teams, &test_event(EventKind::Message)));
    }

    #[test]
    fn test_registry() {
        let mut reg = HandlerRegistry::new();
        let id1 = reg.register(HandlerFilter {
            event_kind: Some(EventKind::Mention),
            command: None,
            pattern: None,
            emoji: None,
            platform: None,
            raw_event_type: None,
        });
        let id2 = reg.register(HandlerFilter {
            event_kind: Some(EventKind::Message),
            command: None,
            pattern: None,
            emoji: None,
            platform: None,
            raw_event_type: None,
        });

        let mention = test_event(EventKind::Mention);
        assert_eq!(reg.match_event(&mention), vec![id1]);

        let message = test_event(EventKind::Message);
        assert_eq!(reg.match_event(&message), vec![id2]);

        reg.unregister(id1);
        assert!(reg.match_event(&mention).is_empty());
    }
}
