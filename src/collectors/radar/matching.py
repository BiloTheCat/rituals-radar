"""Map free text (product names, posts, comments) to Rituals collections.

Matching is deliberately simple and transparent: whole-word, case-insensitive
keyword matching. Social posts only count when they mention the brand, because
words like "karma" or "mehr" are common outside Rituals.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from radar.config import Collection

GENERAL = "rituals_general"  # brand mentioned, but no specific collection
OTHER = "other"              # product that matches no configured collection

_SEPARATORS = r"[\s\-_]+"


def _keyword_pattern(keyword: str) -> str:
    parts = [re.escape(p) for p in re.split(_SEPARATORS, keyword.strip()) if p]
    # Words may be separated by spaces, hyphens (URL slugs) or underscores.
    return _SEPARATORS.join(parts)


def _compile(keywords: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted({_keyword_pattern(k) for k in keywords if k.strip()}, key=len, reverse=True)
    if not alternatives:
        raise ValueError("At least one keyword is required")
    # (?<!\w) / (?!\w) = whole words only, so "sakura" never matches "yozakura".
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


class Matcher:
    def __init__(self, collections: Mapping[str, Collection], brand_terms: Sequence[str]):
        self._collections = [(c.key, _compile(c.keywords)) for c in collections.values()]
        self._brand = _compile(brand_terms)

    def collections_in(self, text: str | None) -> list[str]:
        """All collections mentioned in `text`, in config order."""
        if not text:
            return []
        return [key for key, pattern in self._collections if pattern.search(text)]

    def mentions_brand(self, text: str | None) -> bool:
        return bool(text) and self._brand.search(text) is not None

    def product_collection(self, *texts: str | None) -> str:
        """Collection of a product: the first configured collection that matches."""
        found = self.collections_in(" ".join(t for t in texts if t))
        return found[0] if found else OTHER

    def classify(self, text: str | None, inherited: Sequence[str] = ()) -> tuple[list[str], bool]:
        """Return (matched_collections, is_relevant) for a social post.

        `inherited` is used for comments: a comment under a relevant Rituals video
        is relevant even if it only says "smells amazing".
        """
        own = self.collections_in(text)
        if inherited:
            merged = own + [c for c in inherited if c not in own]
            if len(merged) > 1 and GENERAL in merged:
                merged.remove(GENERAL)
            return merged, True
        if self.mentions_brand(text):
            return (own or [GENERAL]), True
        return own, False


def matcher_from_config(cfg) -> Matcher:  # cfg: radar.config.Config
    return Matcher(cfg.collections, cfg.brand_terms)
