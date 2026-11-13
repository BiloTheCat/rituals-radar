"""Map product names / URL slugs to Rituals collections (product lines).

Whole-word, case-insensitive keyword matching, so "sakura" never matches
"yozakura". The first configured collection that matches wins.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from radar.config import Collection

OTHER = "other"  # product that matches no configured collection

_SEPARATORS = r"[\s\-_]+"


def _keyword_pattern(keyword: str) -> str:
    parts = [re.escape(p) for p in re.split(_SEPARATORS, keyword.strip()) if p]
    # Words may be separated by spaces, hyphens (URL slugs) or underscores.
    return _SEPARATORS.join(parts)


def _compile(keywords: Iterable[str]) -> re.Pattern[str]:
    alternatives = sorted({_keyword_pattern(k) for k in keywords if k.strip()}, key=len, reverse=True)
    if not alternatives:
        raise ValueError("At least one keyword is required")
    return re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)


class Matcher:
    def __init__(self, collections: Mapping[str, Collection]):
        self._collections = [(c.key, _compile(c.keywords)) for c in collections.values()]

    def collections_in(self, text: str | None) -> list[str]:
        """All collections mentioned in `text`, in config order."""
        if not text:
            return []
        return [key for key, pattern in self._collections if pattern.search(text)]

    def product_collection(self, *texts: str | None) -> str:
        """Collection of a product: the first configured collection that matches."""
        found = self.collections_in(" ".join(t for t in texts if t))
        return found[0] if found else OTHER


def matcher_from_config(cfg) -> Matcher:  # cfg: radar.config.Config
    return Matcher(cfg.collections)
