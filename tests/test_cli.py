from __future__ import annotations

from conftest import FakeResponse, FakeSession, fixture_text
from radar import cli, rituals


def test_sample_command(tmp_path):
    assert cli.main(["sample", "--days", "2", "--out", str(tmp_path)]) == 0
    assert list(tmp_path.glob("rituals_stock/*/*.jsonl"))


def test_options_before_the_command(tmp_path):
    assert cli.main(["--out", str(tmp_path), "sample", "--days", "1"]) == 0
    assert list(tmp_path.glob("social/*/*/*.jsonl"))


def test_check_command(capsys):
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert "YOUTUBE_API_KEY" in out and "Watchlist" in out


def test_unknown_social_source():
    assert cli.main(["social", "--sources", "myspace", "--dry-run"]) == 2


def test_stock_dry_run(monkeypatch, capsys):
    page = FakeResponse(200, fixture_text("product_in_stock.html"))
    monkeypatch.setattr(rituals, "make_session", lambda cfg: FakeSession({"https://www.rituals.com/": page}))
    assert cli.main(["stock", "--dry-run"]) == 0
    assert "[dry-run] rituals_stock" in capsys.readouterr().out


def test_stock_fails_loudly_when_every_check_fails(monkeypatch):
    monkeypatch.setattr(rituals, "make_session",
                        lambda cfg: FakeSession({"https://www.rituals.com/": FakeResponse(403, "Forbidden")}))
    assert cli.main(["stock", "--dry-run"]) == 1  # -> red run in GitHub Actions
