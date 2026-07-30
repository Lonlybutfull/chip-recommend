"""Configuration management for Parse1."""

import os
import yaml
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".parse1"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

DEFAULT_CONFIG = {
    "profiles": {
        "default_chip_format": "default",
        "default_model_format": "default",
    },
    "db": {
        "path": "",
    },
    "output": {
        "default_format": "yaml",
    },
}


def ensure_config_dir() -> None:
    """Ensure config directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration from file, merging with defaults."""
    ensure_config_dir()

    config = DEFAULT_CONFIG.copy()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            # Deep merge
            for section in config:
                if section in user_config:
                    config[section].update(user_config[section])
        except Exception:
            pass

    return config


def save_config(config: dict) -> None:
    """Save configuration to file."""
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False)


def get_config(key: str, default: Any = None) -> Any:
    """Get a specific configuration value by dot-separated key.

    Example: get_config("profiles.default_chip_format")
    """
    config = load_config()
    parts = key.split(".")
    value = config
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return default
        if value is None:
            return default
    return value


def set_config(key: str, value: Any) -> None:
    """Set a specific configuration value by dot-separated key."""
    config = load_config()
    parts = key.split(".")
    target = config
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value
    save_config(config)
