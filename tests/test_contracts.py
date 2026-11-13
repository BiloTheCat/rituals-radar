"""The collectors and the Databricks pipeline must agree on the landing-zone schema.

The pipeline files import pyspark, which isn't needed (or installed) for these
tests, so we read the schema constants with `ast` instead of importing them.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from conftest import NOW, REPO_ROOT
from radar.records import CATALOG_FIELDS, STOCK_FIELDS, conform
from radar.sample_data import generate

TRANSFORMATIONS = REPO_ROOT / "src" / "pipeline" / "transformations"
ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def module_constant(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def ddl(const: str) -> dict[str, str]:
    columns = module_constant(TRANSFORMATIONS / "01_bronze.py", const)
    return {c.split(" ", 1)[0]: c.split(" ", 1)[1] for c in columns}


CONTRACTS = [
    ("STOCK_COLUMNS", STOCK_FIELDS, "rituals_stock"),
    ("CATALOG_COLUMNS", CATALOG_FIELDS, "rituals_catalog"),
]


@pytest.mark.parametrize("const, fields, _", CONTRACTS)
def test_bronze_schema_matches_collector_fields(const, fields, _):
    assert list(ddl(const)) == list(fields)


@pytest.mark.parametrize("const, fields, dataset", CONTRACTS)
def test_generated_records_fit_the_declared_types(cfg, const, fields, dataset):
    types = ddl(const)
    data = generate(cfg, days=3, seed=7, now=NOW)
    records = data[dataset]
    assert records, f"no sample records for {dataset}"
    checks = {
        "STRING": lambda v: isinstance(v, str),
        "TIMESTAMP": lambda v: isinstance(v, str) and ISO_UTC.match(v),
        "BOOLEAN": lambda v: isinstance(v, bool),
        "DOUBLE": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "INT": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "BIGINT": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "ARRAY<STRING>": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    }
    for record in records:
        for column, spark_type in types.items():
            value = record[column]
            assert value is None or checks[spark_type](value), f"{dataset}.{column}={value!r} is not {spark_type}"


def test_conform_rejects_unknown_fields():
    with pytest.raises(ValueError):
        conform({"product_id": "1", "typo_field": 1}, STOCK_FIELDS)
    assert conform({"product_id": "1"}, STOCK_FIELDS)["price"] is None


def _keyword_list(path: Path, keyword: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == keyword:
                    return ast.literal_eval(kw.value)
    raise AssertionError(f"{keyword} not found in {path}")


def test_scd2_columns_exist_in_silver():
    """AUTO CDC fails at runtime if it refers to columns that silver doesn't have."""
    silver_columns = (set(ddl("STOCK_COLUMNS")) | {"source_file", "ingested_at", "discount_pct", "snapshot_date"})
    history = TRANSFORMATIONS / "03_gold_history.py"
    for keyword in ("track_history_column_list", "except_column_list"):
        missing = set(_keyword_list(history, keyword)) - silver_columns
        assert not missing, f"{keyword} refers to unknown columns {missing}"
