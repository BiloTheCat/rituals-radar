import pytest

from radar.matching import GENERAL, OTHER


@pytest.mark.parametrize("text, expected", [
    ("The Ritual of Sakura foaming shower gel", ["sakura"]),
    ("the-ritual-of-yozakura-foaming-shower-gel", ["yozakura"]),         # not sakura
    ("YOZAKURA en Sakura naast elkaar", ["yozakura", "sakura"]),         # config order
    ("amsterdam-collection-foaming-shower-gel", ["amsterdam_collection"]),
    ("Velvet Oudh stone diffuser", ["oudh"]),
    ("karmageddon", []),                                                 # whole words only
    ("", []),
    (None, []),
])
def test_collections_in(matcher, text, expected):
    assert matcher.collections_in(text) == expected


@pytest.mark.parametrize("text, collections, relevant", [
    ("Love my Rituals Karma shower foam", ["karma"], True),
    ("Good karma comes back to you", ["karma"], False),            # no brand: not about Rituals
    ("The Ritual of Jing helps me sleep", ["jing"], True),         # "the ritual of" counts as brand
    ("Just bought something at Rituals!", [GENERAL], True),
    ("#rituals haul today", [GENERAL], True),
    ("morning ritual with coffee", [], False),                     # "ritual" is not the brand
])
def test_classify_posts(matcher, text, collections, relevant):
    assert matcher.classify(text) == (collections, relevant)


def test_comment_inherits_video_collections(matcher):
    assert matcher.classify("smells amazing!", inherited=["sakura"]) == (["sakura"], True)
    assert matcher.classify("what about Karma?", inherited=[GENERAL]) == (["karma"], True)
    assert matcher.classify("and Karma?", inherited=["sakura"]) == (["karma", "sakura"], True)


def test_product_collection(matcher):
    assert matcher.product_collection("premium-advent-calendar-2026") == "advent"
    assert matcher.product_collection("The Ritual of Sakura - Advent edition") == "advent"  # first in config wins
    assert matcher.product_collection("candle-surprise-box") == OTHER
    assert matcher.product_collection(None, "rituals-homme-shower-foam") == "homme"
