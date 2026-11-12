from __future__ import annotations

import json

import pytest

from conftest import NOW
from radar.landing import LandingWriter, _volume_root


def test_local_layout(tmp_path):
    writer = LandingWriter(str(tmp_path))
    assert writer.mode == "local"
    path = writer.write("social", [{"a": 1, "text": "héél lekker 😍"}], run_id="abc", when=NOW, subfolder="youtube")
    assert path == f"{tmp_path}/social/youtube/2026-11-12/youtube_20261112T180000Z_abc.jsonl"
    content = (tmp_path / "social/youtube/2026-11-12/youtube_20261112T180000Z_abc.jsonl").read_text(encoding="utf-8")
    assert json.loads(content) == {"a": 1, "text": "héél lekker 😍"}  # UTF-8, one JSON object per line


def test_nothing_to_write(tmp_path):
    assert LandingWriter(str(tmp_path)).write("rituals_stock", [], run_id="x", when=NOW) is None
    assert not any(tmp_path.iterdir())


def test_volume_paths():
    assert _volume_root("/Volumes/workspace/rituals_radar/landing/x/y") == "/Volumes/workspace/rituals_radar/landing"
    assert _volume_root("/Volumes/workspace") is None
    assert _volume_root("./landing_local") is None
    # A volume path that doesn't exist on this machine -> upload through the Databricks Files API
    assert LandingWriter("/Volumes/workspace/rituals_radar/landing").mode == "files_api"


def test_files_api_upload_uses_the_sdk():
    class FakeFiles:
        def __init__(self):
            self.calls = []

        def create_directory(self, path):
            self.calls.append(("mkdir", path))

        def upload(self, path, contents, overwrite):
            self.calls.append(("upload", path, contents.read(), overwrite))

    class FakeClient:
        files = FakeFiles()

    writer = LandingWriter("/Volumes/workspace/rituals_radar/landing")
    writer._client = FakeClient()
    writer.write("rituals_stock", [{"x": 1}], run_id="r9", when=NOW)
    folder = "/Volumes/workspace/rituals_radar/landing/rituals_stock/2026-11-12"
    assert FakeClient.files.calls == [
        ("mkdir", folder),
        ("upload", f"{folder}/rituals_stock_20261112T180000Z_r9.jsonl", b'{"x": 1}\n', False),
    ]


def test_empty_target_is_rejected():
    with pytest.raises(ValueError):
        LandingWriter("")
