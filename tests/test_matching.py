import pytest

from radar.matching import OTHER


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


def test_product_collection(matcher):
    assert matcher.product_collection("premium-advent-calendar-2026") == "advent"
    assert matcher.product_collection("The Ritual of Sakura - Advent edition") == "advent"  # first in config wins
    assert matcher.product_collection("candle-surprise-box") == OTHER
    assert matcher.product_collection(None, "rituals-homme-shower-foam") == "homme"
