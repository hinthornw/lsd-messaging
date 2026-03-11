use thiserror::Error;

#[derive(Debug, Error)]
pub enum LsmsgError {
    #[error("invalid payload: {0}")]
    InvalidPayload(String),

    #[error("platform not supported: {feature} on {platform}")]
    PlatformNotSupported { feature: String, platform: String },

    #[error("config error: {0}")]
    Config(String),

    #[error("invalid pattern: {0}")]
    InvalidPattern(String),
}

pub type Result<T> = std::result::Result<T, LsmsgError>;
