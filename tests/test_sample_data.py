from __future__ import annotations

import json
from pathlib import Path

from conftest import NOW
from radar.landing import LandingWriter
from radar.sample_data import generate, write_sample_data

BRONZE_GLOBS = {  # must match the Auto Loader paths in 01_bronze.py
    "rituals_stock": "rituals_stock/*/*.jsonl",
    "rituals_catalog": "rituals_catalog/*/*.jsonl",
    "rituals_stores": "rituals_stores/*/*.jsonl",
    "reference_pc4": "reference_pc4/*/*.jsonl",
}


def test_generation_is_deterministic(cfg):
    assert generate(cfg, days=5, seed=3, now=NOW) == generate(cfg, days=5, seed=3, now=NOW)


def test_sample_data_shape(cfg):
    data = generate(cfg, days=30, seed=42, now=NOW)
    assert set(data) == {"rituals_stock", "rituals_catalog", "rituals_stores", "reference_pc4"}
    every = [r for records in data.values() for r in records]
    assert all(r["is_sample"] is True for r in every)

    stock = data["rituals_stock"]
    assert len({r["product_id"] for r in stock}) == 14  # the Uukumwe limited edition
    assert max(r["scraped_at"] for r in stock) <= "2026-11-12T18:00:00Z"  # nothing in the future
    out_share = sum(not r["is_available"] for r in stock) / len(stock)
    assert 0.0 < out_share < 0.25, out_share

    launches = {r["product_id"] for r in data["rituals_catalog"]} - {r["product_id"] for r in stock}
    assert launches == {"1199001", "1199002"}


def test_written_files_match_the_pipeline_paths(cfg, tmp_path: Path):
    counts = write_sample_data(cfg, LandingWriter(str(tmp_path)), days=4, seed=1, now=NOW)
    for dataset, pattern in BRONZE_GLOBS.items():
        files = sorted(tmp_path.glob(pattern))
        assert files, f"no files for {dataset} at {pattern}"
        lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in files)
        expected = counts[dataset]
        assert lines == expected
        json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    assert not list(tmp_path.rglob("*.tmp"))
