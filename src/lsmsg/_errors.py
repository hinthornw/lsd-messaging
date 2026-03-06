from __future__ import annotations


class LsmsgError(Exception):
    pass


class PlatformNotSupported(LsmsgError):
    def __init__(self, feature: str, platform: str) -> None:
        self.feature = feature
        self.platform = platform
        super().__init__(f"{feature} is not supported on {platform}")


class ConfigError(LsmsgError):
    pass
