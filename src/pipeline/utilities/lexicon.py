"""Tiny Dutch/English sentiment lexicon - the fallback sentiment engine.

The pipeline uses Databricks' ai_analyze_sentiment() by default. If AI
Functions are not available in your workspace, set the bundle variable
`sentiment_engine` to "lexicon" and this function is used instead (as a Python
UDF). It is intentionally simple: word lists, a few phrases, emoji and
"niet/not" negation. Good enough for a dashboard, and easy to explain.

Pure Python on purpose: unit tested without Spark (tests/test_lexicon.py).
"""

from __future__ import annotations

import re

POSITIVE = {
    # Dutch
    "heerlijk", "heerlijke", "lekker", "lekkere", "mooi", "mooie", "prachtig", "prachtige", "fijn", "fijne",
    "superfijn", "top", "geweldig", "geweldige", "blij", "favoriet", "favoriete", "aanrader", "perfect",
    "perfecte", "zalig", "genieten", "genoten", "verliefd", "vrolijk", "fantastisch", "luxe", "wauw", "wow",
    "toppertje", "aanbevolen", "zacht", "zachte", "verrukkelijk", "dankbaar",
    # English
    "love", "loved", "loving", "lovely", "amazing", "great", "beautiful", "gorgeous", "obsessed",
    "favourite", "favorite", "best", "nice", "recommend", "wonderful", "awesome", "fantastic", "divine",
    "incredible", "excellent", "happy", "soft", "relaxing", "luxurious", "stunning", "yummy",
}
NEGATIVE = {
    # Dutch
    "duur", "vies", "vieze", "jammer", "teleurgesteld", "teleurstellend", "slecht", "slechte", "irritant",
    "frustrerend", "lekkend", "lekt", "kapot", "uitverkocht", "stinkt", "allergie", "allergisch", "nep",
    "boos", "klacht", "waardeloos", "zonde", "geïrriteerd", "geirriteerd", "irritatie", "flauw", "schandalig",
    # English
    "hate", "hated", "bad", "terrible", "awful", "disappointed", "disappointing", "annoying", "annoyed",
    "expensive", "overpriced", "broken", "leaking", "leaked", "irritated", "rash", "worst", "cheap",
    "waste", "useless", "angry", "horrible", "rip-off", "ripoff", "fake", "allergic",
}
NEGATIVE_PHRASES = (
    "sold out", "out of stock", "niet leverbaar", "niet op voorraad", "not a fan", "te duur", "te hoog",
    "too expensive", "too strong", "too high", "niet meer verkrijgbaar", "no longer available",
)
NEGATORS = {"niet", "geen", "nooit", "not", "no", "never", "dont", "don't", "isn't", "isnt", "wasn't"}
POSITIVE_EMOJI = set("😍🥰❤✨👍😊🤩💕💖🙌💯🌸")
NEGATIVE_EMOJI = set("😡👎🤢😤😒💔🙄😠🤮")

_WORD_RE = re.compile(r"[a-zà-ÿ'\-]+")


def score_sentiment(text: str | None) -> str | None:
    """Return 'positive', 'negative', 'neutral', 'mixed', or None for empty text."""
    if text is None or not text.strip():
        return None
    lowered = text.lower()
    positive = negative = 0

    for phrase in NEGATIVE_PHRASES:
        hits = lowered.count(phrase)
        if hits:
            negative += hits
            lowered = lowered.replace(phrase, " ")  # don't count its words twice

    words = _WORD_RE.findall(lowered)
    for i, word in enumerate(words):
        negated = any(w in NEGATORS for w in words[max(0, i - 2):i])
        if word in POSITIVE:
            if negated:
                negative += 1
            else:
                positive += 1
        elif word in NEGATIVE:
            if negated:
                positive += 1  # "niet slecht" / "not bad"
            else:
                negative += 1

    positive += sum(1 for ch in text if ch in POSITIVE_EMOJI)
    negative += sum(1 for ch in text if ch in NEGATIVE_EMOJI)

    if positive == 0 and negative == 0:
        return "neutral"
    if positive and negative and abs(positive - negative) <= 1:
        return "mixed"
    return "positive" if positive > negative else "negative"
