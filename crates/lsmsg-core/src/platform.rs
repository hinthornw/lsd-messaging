use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Platform {
    Slack,
    Teams,
    Discord,
    Telegram,
    Github,
    Linear,
    Gchat,
}

impl Platform {
    pub fn as_str(&self) -> &str {
        match self {
            Platform::Slack => "slack",
            Platform::Teams => "teams",
            Platform::Discord => "discord",
            Platform::Telegram => "telegram",
            Platform::Github => "github",
            Platform::Linear => "linear",
            Platform::Gchat => "gchat",
        }
    }
}

impl std::fmt::Display for Platform {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlatformCapabilities {
    pub name: Platform,
    pub ephemeral: bool,
    pub threads: bool,
    pub reactions: bool,
    pub streaming: bool,
    pub modals: bool,
    pub typing_indicator: bool,
}

pub fn slack() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Slack,
        ephemeral: true,
        threads: true,
        reactions: true,
        streaming: true,
        modals: true,
        typing_indicator: true,
    }
}

pub fn teams() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Teams,
        ephemeral: false,
        threads: true,
        reactions: false,
        streaming: false,
        modals: false,
        typing_indicator: true,
    }
}

pub fn discord() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Discord,
        ephemeral: false,
        threads: true,
        reactions: true,
        streaming: false,
        modals: false,
        typing_indicator: true,
    }
}

pub fn telegram() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Telegram,
        ephemeral: false,
        threads: false,
        reactions: true,
        streaming: false,
        modals: false,
        typing_indicator: true,
    }
}

pub fn github() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Github,
        ephemeral: false,
        threads: true,
        reactions: false,
        streaming: false,
        modals: false,
        typing_indicator: false,
    }
}

pub fn linear() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Linear,
        ephemeral: false,
        threads: true,
        reactions: true,
        streaming: false,
        modals: false,
        typing_indicator: false,
    }
}

pub fn gchat() -> PlatformCapabilities {
    PlatformCapabilities {
        name: Platform::Gchat,
        ephemeral: true,
        threads: true,
        reactions: true,
        streaming: false,
        modals: false,
        typing_indicator: true,
    }
}

pub fn capabilities_for(platform: &Platform) -> PlatformCapabilities {
    match platform {
        Platform::Slack => slack(),
        Platform::Teams => teams(),
        Platform::Discord => discord(),
        Platform::Telegram => telegram(),
        Platform::Github => github(),
        Platform::Linear => linear(),
        Platform::Gchat => gchat(),
    }
}
