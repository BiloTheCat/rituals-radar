"""The fallback sentiment engine (used when AI Functions are unavailable)."""

import pytest

from radar.sample_data import TEMPLATES
from utilities.lexicon import score_sentiment

EXPECTED = {"positive": "positive", "negative": "negative", "stockout": "negative",
            "neutral": "neutral", "mixed": "mixed"}


@pytest.mark.parametrize("mood, lang, template",
                         [(mood, lang, t) for mood, items in TEMPLATES.items() for lang, t in items])
def test_sample_texts_get_the_intended_sentiment(mood, lang, template):
    text = template.format(name="The Ritual of Sakura")
    assert score_sentiment(text) == EXPECTED[mood], text


@pytest.mark.parametrize("text, expected", [
    ("Deze geur is niet lekker", "negative"),        # negation flips
    ("Honestly not bad at all", "positive"),
    ("Mooi, maar veel te duur en jammer van de verpakking", "mixed"),
    ("Te duur, lekt en ik ben teleurgesteld", "negative"),
    ("Besteld op maandag", "neutral"),
    ("😍😍", "positive"),
    ("", None),
    (None, None),
])
def test_rules(text, expected):
    assert score_sentiment(text) == expected
