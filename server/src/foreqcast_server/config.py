"""Server configuration via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    """Immutable server configuration loaded from environment."""

    # Directory containing forecast parquet files to serve
    forecast_dir: Path = field(default_factory=lambda: Path(os.environ.get("FOREQCAST_DIR", "./forecasts")))

    # Comma-separated list of valid API keys
    api_keys: frozenset[str] = field(
        default_factory=lambda: frozenset(
            k.strip() for k in os.environ.get("FOREQCAST_API_KEYS", "").split(",") if k.strip()
        )
    )

    # Server bind
    host: str = field(default_factory=lambda: os.environ.get("FOREQCAST_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("FOREQCAST_PORT", "8400")))

    def validate(self) -> None:
        """Raise if config is invalid."""
        if not self.api_keys:
            raise ValueError(
                "FOREQCAST_API_KEYS must be set to a comma-separated list of API keys "
                "(e.g. FOREQCAST_API_KEYS=key1,key2)"
            )
        if not self.forecast_dir.is_dir():
            raise ValueError(f"FOREQCAST_DIR does not exist or is not a directory: {self.forecast_dir}")


def load_config() -> ServerConfig:
    """Load and validate server config from environment."""
    cfg = ServerConfig()
    cfg.validate()
    return cfg
