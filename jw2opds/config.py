from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

DEFAULTS = {
    "languages": ["en"],
    "output_dir": "output",
    "cache_dir": "cache",
    "periodical_keys": ["w", "wp", "wqu", "g", "mwb", "ws"],
    "periodical_lookback_months": 0,
    "recheck_days": 30,
    "concurrency": 6,
    "request_timeout": 15,
    "base_url": "",
    "user_agent": "jw2opds/1.0 (+personal OPDS catalog generator; https://www.jw.org publications)",
    "epub_proxy": {"base_url": "", "devices": []},
}


@dataclasses.dataclass
class Config:
    languages: list
    output_dir: Path
    cache_dir: Path
    periodical_keys: list
    periodical_lookback_months: int
    recheck_days: int
    concurrency: int
    request_timeout: int
    base_url: str
    user_agent: str
    epub_proxy_base_url: str
    epub_proxy_devices: list

    @property
    def state_db_path(self) -> Path:
        return self.cache_dir / "state.sqlite3"

    @property
    def jw_catalog_db_path(self) -> Path:
        return self.cache_dir / "jw_catalog.db"

    @property
    def jw_catalog_manifest_path(self) -> Path:
        return self.cache_dir / "jw_catalog_manifest.json"


def load_config(path: str | Path) -> Config:
    path = Path(path)
    data = dict(DEFAULTS)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        data.update(loaded)
    else:
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.yaml to get started."
        )

    if not data["languages"]:
        raise ValueError("config: 'languages' must contain at least one language code")

    epub_proxy = dict(DEFAULTS["epub_proxy"])
    epub_proxy.update(data.get("epub_proxy") or {})

    return Config(
        languages=list(data["languages"]),
        output_dir=Path(data["output_dir"]),
        cache_dir=Path(data["cache_dir"]),
        periodical_keys=list(data["periodical_keys"]),
        periodical_lookback_months=int(data["periodical_lookback_months"]),
        recheck_days=int(data["recheck_days"]),
        concurrency=int(data["concurrency"]),
        request_timeout=int(data["request_timeout"]),
        base_url=str(data["base_url"] or ""),
        user_agent=str(data["user_agent"]),
        epub_proxy_base_url=str(epub_proxy["base_url"] or ""),
        epub_proxy_devices=list(epub_proxy["devices"] or []),
    )
