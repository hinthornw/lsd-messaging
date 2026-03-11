use crate::event::Event;
use crate::handler::HandlerRegistry;
use crate::slack;
use crate::teams;

pub struct DispatchPlan {
    pub event: Event,
    pub handler_ids: Vec<u64>,
}

pub enum WebhookOutcome {
    Rejected { status_code: u16, message: String },
    Challenge(String),
    Ignored,
    Dispatch(Box<DispatchPlan>),
}

pub fn process_slack_webhook(
    body: &[u8],
    content_type: &str,
    signing_secret: Option<&str>,
    timestamp: Option<&str>,
    signature: Option<&str>,
    registry: &HandlerRegistry,
) -> WebhookOutcome {
    if let Some(secret) = signing_secret {
        let ts = match timestamp {
            Some(ts) if !ts.is_empty() => ts,
            _ => {
                return WebhookOutcome::Rejected {
                    status_code: 401,
                    message: "missing signature headers".into(),
                };
            }
        };
        let sig = match signature {
            Some(sig) if !sig.is_empty() => sig,
            _ => {
                return WebhookOutcome::Rejected {
                    status_code: 401,
                    message: "missing signature headers".into(),
                };
            }
        };

        if !slack::verify_signature(secret, ts, sig, body) {
            return WebhookOutcome::Rejected {
                status_code: 401,
                message: "invalid signature".into(),
            };
        }
    }

    match slack::parse_webhook(body, content_type, &std::collections::HashMap::new()) {
        slack::SlackWebhookResult::Challenge(challenge) => WebhookOutcome::Challenge(challenge),
        slack::SlackWebhookResult::Ignored => WebhookOutcome::Ignored,
        slack::SlackWebhookResult::Event(event) => dispatch_or_ignore(*event, registry),
    }
}

pub fn process_teams_webhook(body: &[u8], registry: &HandlerRegistry) -> WebhookOutcome {
    let payload = match serde_json::from_slice(body) {
        Ok(payload) => payload,
        Err(_) => {
            return WebhookOutcome::Rejected {
                status_code: 400,
                message: "invalid json".into(),
            };
        }
    };

    match teams::parse_webhook(&payload) {
        Some(event) => dispatch_or_ignore(event, registry),
        None => WebhookOutcome::Ignored,
    }
}

fn dispatch_or_ignore(event: Event, registry: &HandlerRegistry) -> WebhookOutcome {
    let handler_ids = registry.match_event(&event);
    if handler_ids.is_empty() {
        WebhookOutcome::Ignored
    } else {
        WebhookOutcome::Dispatch(Box::new(DispatchPlan { event, handler_ids }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::EventKind;
    use crate::platform::Platform;

    #[test]
    fn slack_challenge_bypasses_registry() {
        let registry = HandlerRegistry::new();
        let outcome = process_slack_webhook(
            br#"{"type":"url_verification","challenge":"abc123"}"#,
            "application/json",
            None,
            None,
            None,
            &registry,
        );

        match outcome {
            WebhookOutcome::Challenge(challenge) => assert_eq!(challenge, "abc123"),
            _ => panic!("expected challenge"),
        }
    }

    #[test]
    fn slack_rejects_missing_signature_headers_when_secret_configured() {
        let registry = HandlerRegistry::new();
        let outcome = process_slack_webhook(
            br#"{"type":"event_callback","event":{"type":"message"}}"#,
            "application/json",
            Some("secret"),
            None,
            None,
            &registry,
        );

        match outcome {
            WebhookOutcome::Rejected {
                status_code,
                message,
            } => {
                assert_eq!(status_code, 401);
                assert_eq!(message, "missing signature headers");
            }
            _ => panic!("expected rejection"),
        }
    }

    #[test]
    fn slack_dispatches_matching_handler_ids() {
        let mut registry = HandlerRegistry::new();
        let handler_id = registry
            .register_from_fields(
                Some(EventKind::Mention),
                None,
                Some("hello"),
                None,
                Some(Platform::Slack),
                None,
            )
            .unwrap();

        let outcome = process_slack_webhook(
            br#"{"type":"event_callback","team_id":"T1","event":{"type":"app_mention","text":"<@U1> hello","channel":"C1","ts":"123.456","user":"U1"}}"#,
            "application/json",
            None,
            None,
            None,
            &registry,
        );

        match outcome {
            WebhookOutcome::Dispatch(plan) => {
                assert_eq!(plan.event.kind, EventKind::Mention);
                assert_eq!(plan.handler_ids, vec![handler_id]);
            }
            _ => panic!("expected dispatch"),
        }
    }

    #[test]
    fn teams_invalid_json_is_rejected() {
        let registry = HandlerRegistry::new();
        let outcome = process_teams_webhook(b"not json", &registry);

        match outcome {
            WebhookOutcome::Rejected {
                status_code,
                message,
            } => {
                assert_eq!(status_code, 400);
                assert_eq!(message, "invalid json");
            }
            _ => panic!("expected rejection"),
        }
    }

    #[test]
    fn teams_ignored_when_no_handlers_match() {
        let registry = HandlerRegistry::new();
        let outcome = process_teams_webhook(
            br#"{"type":"message","text":"hello","from":{"id":"U1"},"conversation":{"id":"conv-1","tenantId":"tenant-1"},"channelData":{"tenant":{"id":"tenant-1"},"team":{"id":"team-1"}},"id":"msg-1"}"#,
            &registry,
        );

        match outcome {
            WebhookOutcome::Ignored => {}
            _ => panic!("expected ignored"),
        }
    }
}
