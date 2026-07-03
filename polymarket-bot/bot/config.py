"""Environment + YAML configuration loading.

Secrets come exclusively from .env / process environment; config.yaml holds
only non-secret tunables. Live trading requires BOTH the yaml flag and the
DRY_RUN env var to be switched off, so a stray yaml edit can't go live alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised for missing secrets or invalid configuration at startup."""


@dataclass
class Secrets:
    private_key: str
    proxy_wallet: str
    clob_api_key: str
    clob_api_secret: str
    clob_api_passphrase: str
    news_api_key: str
    dry_run_env: bool


@dataclass
class Config:
    raw: dict[str, Any]
    secrets: Secrets
    base_dir: Path

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def dry_run(self) -> bool:
        # Live only when BOTH switches are explicitly off.
        return bool(self.raw["runtime"].get("dry_run", True)) or self.secrets.dry_run_env

    @property
    def db_path(self) -> Path:
        return self.base_dir / self.raw["runtime"]["database_path"]

    @property
    def categories(self) -> list[str]:
        return list(self.raw["categories"])

    @property
    def conviction_thresholds(self) -> list[int]:
        return list(self.raw["conviction_thresholds"])


_REQUIRED_ENV = {
    "private_key": "POLYMARKET_PRIVATE_KEY",
    "proxy_wallet": "POLYMARKET_PROXY_WALLET",
    "clob_api_key": "CLOB_API_KEY",
    "clob_api_secret": "CLOB_API_SECRET",
    "clob_api_passphrase": "CLOB_API_PASSPHRASE",
}


def load_config(base_dir: str | Path | None = None) -> Config:
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
    load_dotenv(base / ".env")

    cfg_path = base / "config.yaml"
    if not cfg_path.exists():
        raise ConfigError(f"config.yaml not found at {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    dry_run_env = os.getenv("DRY_RUN", "true").strip().lower() != "false"

    values: dict[str, str] = {}
    missing: list[str] = []
    for attr, env_name in _REQUIRED_ENV.items():
        val = os.getenv(env_name, "").strip()
        placeholder = not val or "YOUR" in val.upper() or val.startswith("your-")
        if placeholder:
            missing.append(env_name)
            val = ""
        values[attr] = val

    # In dry-run mode we tolerate missing trading secrets (signal research only);
    # going live without them is a hard setup error.
    if missing and not dry_run_env:
        raise ConfigError(
            "Live trading requested (DRY_RUN=false) but these .env values are "
            f"missing or placeholders: {', '.join(missing)}"
        )

    secrets = Secrets(
        private_key=values["private_key"],
        proxy_wallet=values["proxy_wallet"],
        clob_api_key=values["clob_api_key"],
        clob_api_secret=values["clob_api_secret"],
        clob_api_passphrase=values["clob_api_passphrase"],
        news_api_key=os.getenv("NEWS_API_KEY", "").strip(),
        dry_run_env=dry_run_env,
    )
    return Config(raw=raw, secrets=secrets, base_dir=base)
