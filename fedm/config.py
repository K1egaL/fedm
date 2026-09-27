"""Persistent user config at ~/.config/fedm/config.json."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .providers import DEFAULT_DOMAINS

CONFIG_DIR = Path.home() / ".config" / "fedm"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    selected_providers: list[str] = field(
        default_factory=lambda: ["cloudflare", "google", "quad9"]
    )
    custom_targets: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=lambda: list(DEFAULT_DOMAINS))
    iterations: int = 6
    timeout: float = 2.0
    enable_icmp: bool = False
    theme: str = "auto"  # auto | light | dark


def load() -> Config:
    if not CONFIG_FILE.exists():
        return Config()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()
    cfg = Config()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def save(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(asdict(cfg), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )