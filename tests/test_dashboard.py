"""Static checks on the AI/BI dashboard definition (.lvdash.json).

A broken field name or version number doesn't fail a deploy, it just renders an
empty widget. These checks catch that before it reaches Databricks.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import REPO_ROOT

DASHBOARD = REPO_ROOT / "src" / "dashboards" / "rituals_radar.lvdash.json"
TRANSFORMATIONS = REPO_ROOT / "src" / "pipeline" / "transformations"
SPEC_VERSIONS = {"counter": 2, "table": 2, "bar": 3, "line": 3, "scatter": 3, "pie": 3, "area": 3,
                 "filter-single-select": 2, "filter-multi-select": 2}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


@pytest.fixture(scope="module")
def dashboard():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def widgets(dashboard):
    for page in dashboard["pages"]:
        for item in page["layout"]:
            yield page, item["widget"], item["position"]


def pipeline_tables() -> set[str]:
    names = set()
    for path in TRANSFORMATIONS.iterdir():
        if path.suffix not in (".py", ".sql"):
            continue
        text = path.read_text(encoding="utf-8")
        names |= set(re.findall(r'name="(\w+)"', text))
        names |= set(re.findall(r"MATERIALIZED VIEW (\w+)", text))
    return names


def test_pages(dashboard):
    assert dashboard["pages"]
    for page in dashboard["pages"]:
        assert page["pageType"] == "PAGE_TYPE_CANVAS"
        assert page["layoutVersion"] == "GRID_V1"  # without it, widget sizes are ignored


def test_widget_names_are_unique_and_valid(dashboard):
    names = [w["name"] for _, w, _ in widgets(dashboard)]
    assert len(names) == len(set(names))
    assert all(NAME_RE.match(n) for n in names)


def test_positions_fit_the_grid_without_overlap(dashboard):
    for page in dashboard["pages"]:
        taken = set()
        for item in page["layout"]:
            pos = item["position"]
            assert pos["x"] >= 0 and pos["width"] > 0 and pos["x"] + pos["width"] <= 12, item["widget"]["name"]
            cells = {(x, y) for x in range(pos["x"], pos["x"] + pos["width"])
                     for y in range(pos["y"], pos["y"] + pos["height"])}
            assert not cells & taken, f"overlap at {item['widget']['name']}"
            taken |= cells


def test_queries_and_encodings_line_up(dashboard):
    datasets = {d["name"] for d in dashboard["datasets"]}
    used = set()
    for _, widget, _ in widgets(dashboard):
        if "multilineTextboxSpec" in widget:
            assert "spec" not in widget  # text widgets have no spec
            continue
        spec = widget["spec"]
        assert spec["version"] == SPEC_VERSIONS[spec["widgetType"]], widget["name"]
        if spec["widgetType"].startswith("filter-"):
            names = {q["name"] for q in widget["queries"]}
            for binding in spec["encodings"]["fields"]:
                assert binding["queryName"] in names, widget["name"]  # every binding needs its query
            for q in widget["queries"]:
                assert q["query"]["datasetName"] in datasets and q["query"]["disaggregated"] is False
                assert {f["name"] for f in q["query"]["fields"]} >= {b["fieldName"] for b in spec["encodings"]["fields"]
                                                                    if b["queryName"] == q["name"]}
            continue
        (query,) = widget["queries"]
        assert query["query"]["datasetName"] in datasets
        used.add(query["query"]["datasetName"])
        fields = {f["name"] for f in query["query"]["fields"]}
        encodings = spec["encodings"]
        referenced = [c["fieldName"] for c in encodings.get("columns", [])]
        referenced += [e["fieldName"] for k, e in encodings.items() if k != "columns"]
        assert set(referenced) <= fields, f"{widget['name']}: {set(referenced) - fields} not in query fields"
        aggregated = any("(" in f["expression"] for f in query["query"]["fields"])
        assert query["query"]["disaggregated"] is (not aggregated), widget["name"]
    assert used == datasets, f"unused datasets: {datasets - used}"


def test_dataset_sql_only_uses_pipeline_tables(dashboard):
    known = pipeline_tables()
    for dataset in dashboard["datasets"]:
        sql = "".join(dataset["queryLines"])
        assert not sql.rstrip().endswith(";")
        for table in re.findall(r"workspace\.rituals_radar\.(\w+)", sql):
            assert table in known, f"{dataset['name']} uses unknown table {table}"


def test_dataset_aliases_match_widget_fields(dashboard):
    """Every simple field a widget asks for must be a column alias in the dataset SQL."""
    sql_by_name = {d["name"]: "".join(d["queryLines"]) for d in dashboard["datasets"]}
    for _, widget, _ in widgets(dashboard):
        if "queries" not in widget:
            continue
        query = widget["queries"][0]["query"]
        sql = sql_by_name[query["datasetName"]]
        for field in query["fields"]:
            column = re.search(r"`(\w+)`", field["expression"]).group(1)
            assert re.search(rf"\b{column}\b", sql), f"{widget['name']}: column {column} not produced by dataset SQL"
