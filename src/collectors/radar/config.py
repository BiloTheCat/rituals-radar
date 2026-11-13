"""Load and validate config/radar.toml into typed settings objects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover - only on old runtimes
    raise RuntimeError(
        "Python 3.11 or newer is required (tomllib). In Databricks, pick "
        "environment version 2 or higher in the notebook's Environment panel."
    ) from exc


@dataclass(frozen=True)
class Collection:
    key: str
    display_name: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class SiteConfig:
    base_url: str = "https://www.rituals.com"
    locale: str = "nl-nl"
    delay_seconds: float = 3.0
    timeout_seconds: float = 20.0
    user_agent: str = "RitualsRadar/1.0 (non-commercial portfolio project)"
    respect_robots_txt: bool = True


@dataclass(frozen=True)
class WatchlistConfig:
    products: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    max_products: int = 40


@dataclass(frozen=True)
class Config:
    path: Path
    volume_path: str
    site: SiteConfig
    watchlist: WatchlistConfig
    collections: dict[str, Collection]


def _tuple(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{name}' must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def load_config(path: str | os.PathLike[str] = "config/radar.toml") -> Config:
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    site_raw = raw.get("site", {})
    site = SiteConfig(
        base_url=str(site_raw.get("base_url", SiteConfig.base_url)).rstrip("/"),
        locale=str(site_raw.get("locale", SiteConfig.locale)),
        delay_seconds=float(site_raw.get("delay_seconds", SiteConfig.delay_seconds)),
        timeout_seconds=float(site_raw.get("timeout_seconds", SiteConfig.timeout_seconds)),
        user_agent=str(site_raw.get("user_agent", SiteConfig.user_agent)),
        respect_robots_txt=bool(site_raw.get("respect_robots_txt", True)),
    )
    if site.delay_seconds < 1.0:
        raise ValueError("site.delay_seconds must be at least 1 second (be polite to rituals.com)")

    collections: dict[str, Collection] = {}
    for key, value in raw.get("collections", {}).items():
        keywords = _tuple(value.get("keywords"), f"collections.{key}.keywords")
        if not keywords:
            raise ValueError(f"collections.{key} needs at least one keyword")
        collections[key] = Collection(
            key=key,
            display_name=str(value.get("display_name", key.replace("_", " ").title())),
            keywords=tuple(k.lower() for k in keywords),
        )

    wl_raw = raw.get("watchlist", {})
    watchlist = WatchlistConfig(
        products=_tuple(wl_raw.get("products"), "watchlist.products"),
        collections=_tuple(wl_raw.get("collections"), "watchlist.collections"),
        max_products=int(wl_raw.get("max_products", 40)),
    )
    unknown = [c for c in watchlist.collections if c not in collections]
    if unknown:
        raise ValueError(f"watchlist.collections refers to unknown collections: {unknown}")

    return Config(
        path=path,
        volume_path=str(raw.get("landing", {}).get("volume_path", "")).rstrip("/"),
        site=site,
        watchlist=watchlist,
        collections=collections,
    )
