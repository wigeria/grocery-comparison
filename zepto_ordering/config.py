"""App settings and file locations.

Settings are loaded from config.toml at the project root (override with
ZEPTO_CONFIG_FILE). Runtime files live inside the project directory:
the SQLite database and its migrations in db/, tokens and uploads in data/.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path(os.environ.get("ZEPTO_CONFIG_FILE", PROJECT_ROOT / "config.toml"))
DATA_DIR = Path(os.environ.get("ZEPTO_DATA_DIR", PROJECT_ROOT / "data"))
UPLOADS_DIR = DATA_DIR / "uploads"
CLAUDE_WORK_DIR = DATA_DIR / "claude"
DB_DIR = PROJECT_ROOT / "db"
DB_FILE = Path(os.environ.get("ZEPTO_DB_FILE", DB_DIR / "zepto_ordering.sqlite3"))
MIGRATIONS_DIR = DB_DIR / "migrations"


class ConfigError(Exception):
    """Raised when config.toml is missing or incomplete."""


@dataclass(frozen=True)
class Config:
    delivery_address_id: str
    blinkit_latitude: float
    blinkit_longitude: float
    host: str = "0.0.0.0"
    port: int = 8000
    claude_model: str | None = None


def load_config() -> Config:
    """Read and validate config.toml."""
    if not CONFIG_FILE.exists():
        raise ConfigError(
            f"Config file not found: {CONFIG_FILE}. Copy config.example.toml to config.toml."
        )
    data = tomllib.loads(CONFIG_FILE.read_text())

    address_id = data.get("zepto", {}).get("delivery_address_id")
    if not address_id:
        raise ConfigError(f"Missing zepto.delivery_address_id in {CONFIG_FILE}")

    blinkit = data.get("blinkit", {})
    if "latitude" not in blinkit or "longitude" not in blinkit:
        raise ConfigError(f"Missing blinkit.latitude or blinkit.longitude in {CONFIG_FILE}")

    server = data.get("server", {})
    claude = data.get("claude", {})
    return Config(
        delivery_address_id=address_id,
        blinkit_latitude=float(blinkit["latitude"]),
        blinkit_longitude=float(blinkit["longitude"]),
        host=server.get("host", Config.host),
        port=int(server.get("port", Config.port)),
        claude_model=claude.get("model") or None,
    )
