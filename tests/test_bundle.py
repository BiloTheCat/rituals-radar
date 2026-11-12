"""Sanity checks for the bundle configuration (no Databricks connection needed)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT

RESOURCES = REPO_ROOT / "resources"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bundle():
    root = load(REPO_ROOT / "databricks.yml")
    resources: dict = {}
    for path in sorted(RESOURCES.glob("*.yml")):  # same as `include: resources/*.yml`
        for kind, items in load(path)["resources"].items():
            resources.setdefault(kind, {}).update(items)
    return root, resources


def test_variables_are_declared(bundle):
    root, _ = bundle
    declared = set(root["variables"])
    files = [REPO_ROOT / "databricks.yml", *RESOURCES.rglob("*.yml")]
    used = {m for f in files for m in re.findall(r"\$\{var\.(\w+)\}", f.read_text(encoding="utf-8"))}
    assert used <= declared, used - declared


def test_resource_references_exist(bundle):
    _, resources = bundle
    for path in RESOURCES.glob("*.yml"):
        for kind, key in re.findall(r"\$\{resources\.(\w+)\.(\w+)\.", path.read_text(encoding="utf-8")):
            assert key in resources.get(kind, {}), f"{path.name}: unknown resource {kind}.{key}"


def test_pipeline_paths(bundle):
    _, resources = bundle
    (pipeline,) = resources["pipelines"].values()
    assert pipeline["serverless"] is True
    assert (RESOURCES / pipeline["root_path"]).resolve() == REPO_ROOT / "src" / "pipeline"
    include = pipeline["libraries"][0]["glob"]["include"]
    folder = (RESOURCES / include.replace("/**", "")).resolve()
    assert folder.is_dir() and any(folder.iterdir())
    landing = pipeline["configuration"]["rituals_radar.landing_path"]
    assert landing.startswith("/Volumes/${var.catalog}/${var.schema}/")


def test_config_volume_matches_bundle_defaults(bundle, cfg):
    root, resources = bundle
    catalog, schema = root["variables"]["catalog"]["default"], root["variables"]["schema"]["default"]
    (volume,) = resources["volumes"].values()
    assert cfg.volume_path == f"/Volumes/{catalog}/{schema}/{volume['name']}"


def test_dashboard_and_job(bundle):
    _, resources = bundle
    (dashboard,) = resources["dashboards"].values()
    assert (RESOURCES / dashboard["file_path"]).resolve().is_file()
    (job,) = resources["jobs"].values()
    assert job["schedule"]["pause_status"] == "UNPAUSED"
    assert "${resources.pipelines.rituals_radar.id}" in str(job["tasks"])


def test_optional_collector_job_paths_work_once_moved():
    optional = load(RESOURCES / "optional" / "collectors_in_databricks.yml")
    for job in optional["resources"]["jobs"].values():
        for task in job["tasks"]:
            notebook = (RESOURCES / task["notebook_task"]["notebook_path"]).resolve()  # path as seen from resources/
            assert notebook.is_file(), notebook


def test_notebooks_have_the_databricks_header():
    for notebook in (REPO_ROOT / "src" / "notebooks").glob("*.py"):
        assert notebook.read_text(encoding="utf-8").startswith("# Databricks notebook source"), notebook.name


def test_no_dev_mode_on_the_default_target(bundle):
    root, _ = bundle
    target = next(t for t in root["targets"].values() if t.get("default"))
    assert target.get("mode") != "development"  # development mode would pause the schedule
