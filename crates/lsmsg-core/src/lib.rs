pub mod error;
pub mod event;
pub mod handler;
pub mod ingress;
pub mod platform;
pub mod slack;
pub mod teams;

pub use error::{LsmsgError, Result};
pub use event::{deterministic_thread_id, Event, EventKind, UserInfo};
pub use handler::{HandlerFilter, HandlerRegistry};
pub use ingress::{process_slack_webhook, process_teams_webhook, DispatchPlan, WebhookOutcome};
pub use platform::{Platform, PlatformCapabilities};
