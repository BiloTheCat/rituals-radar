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
class YouTubeConfig:
    enabled: bool = True
    search_terms: tuple[str, ...] = ()
    max_videos_per_term: int = 5
    max_comments_per_video: int = 30
    region_code: str = "NL"
    relevance_language: str = ""


@dataclass(frozen=True)
class BlueskyConfig:
    enabled: bool = True
    search_terms: tuple[str, ...] = ()
    max_posts_per_term: int = 50


@dataclass(frozen=True)
class XConfig:
    enabled: bool = True
    query: str = ""
    lookback_hours: int = 24
    max_posts_per_run: int = 50


@dataclass(frozen=True)
class TikTokConfig:
    enabled: bool = True
    links_file: str = "config/tiktok_links.txt"


@dataclass(frozen=True)
class Config:
    path: Path
    volume_path: str
    site: SiteConfig
    watchlist: WatchlistConfig
    brand_terms: tuple[str, ...]
    collections: dict[str, Collection]
    lookback_days: int
    youtube: YouTubeConfig
    bluesky: BlueskyConfig
    x: XConfig
    tiktok: TikTokConfig

    @property
    def repo_root(self) -> Path:
        """Folder that contains config/ (relative paths in the config resolve from here)."""
        return self.path.parent.parent

    def resolve(self, relative: str) -> Path:
        candidate = Path(relative)
        return candidate if candidate.is_absolute() else self.repo_root / candidate


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

    social = raw.get("social", {})
    yt = social.get("youtube", {})
    bs = social.get("bluesky", {})
    xx = social.get("x", {})
    tt = social.get("tiktok", {})

    return Config(
        path=path,
        volume_path=str(raw.get("landing", {}).get("volume_path", "")).rstrip("/"),
        site=site,
        watchlist=watchlist,
        brand_terms=tuple(t.lower() for t in _tuple(raw.get("brand", {}).get("terms"), "brand.terms"))
        or ("rituals",),
        collections=collections,
        lookback_days=int(social.get("lookback_days", 7)),
        youtube=YouTubeConfig(
            enabled=bool(yt.get("enabled", True)),
            search_terms=_tuple(yt.get("search_terms"), "social.youtube.search_terms"),
            max_videos_per_term=int(yt.get("max_videos_per_term", 5)),
            max_comments_per_video=int(yt.get("max_comments_per_video", 30)),
            region_code=str(yt.get("region_code", "NL")),
            relevance_language=str(yt.get("relevance_language", "")),
        ),
        bluesky=BlueskyConfig(
            enabled=bool(bs.get("enabled", True)),
            search_terms=_tuple(bs.get("search_terms"), "social.bluesky.search_terms"),
            max_posts_per_term=int(bs.get("max_posts_per_term", 50)),
        ),
        x=XConfig(
            enabled=bool(xx.get("enabled", True)),
            query=str(xx.get("query", "")),
            lookback_hours=int(xx.get("lookback_hours", 24)),
            max_posts_per_run=int(xx.get("max_posts_per_run", 50)),
        ),
        tiktok=TikTokConfig(
            enabled=bool(tt.get("enabled", True)),
            links_file=str(tt.get("links_file", "config/tiktok_links.txt")),
        ),
    )
