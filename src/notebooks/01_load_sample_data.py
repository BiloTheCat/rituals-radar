# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Load sample data
# MAGIC
# MAGIC Writes ~30 days of **generated** stock checks and sitemap snapshots into the
# MAGIC landing volume, so the pipeline and the dashboard work before any real data has been collected.
# MAGIC Every generated row has `is_sample = true`; the pipeline drops them when the bundle variable
# MAGIC `include_sample_data` is `"false"`.
# MAGIC
# MAGIC **Order:** `databricks bundle deploy` first (it creates the schema + volume), then this notebook,
# MAGIC then run the job `rituals-radar-refresh`. Use serverless compute, environment version 2 or higher.

# COMMAND ----------

dbutils.widgets.text("days", "30", "Days of history")  # noqa: F821
dbutils.widgets.text("catalog", "workspace", "Catalog")  # noqa: F821
dbutils.widgets.text("schema", "rituals_radar", "Schema")  # noqa: F821

# COMMAND ----------

import os
import sys


def find_repo_root() -> str:
    """The bundle syncs the repo into the workspace; find the folder that contains config/radar.toml."""
    candidates = [os.getcwd()]
    try:
        notebook_path = (
            dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()  # noqa: F821
        )
        candidates.append("/Workspace" + os.path.dirname(notebook_path))
    except Exception:  # noqa: BLE001
        pass
    for start in candidates:
        path = start
        for _ in range(5):
            if os.path.exists(os.path.join(path, "config", "radar.toml")):
                return path
            path = os.path.dirname(path)
    raise FileNotFoundError("Could not find config/radar.toml above this notebook")


REPO_ROOT = find_repo_root()
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "collectors"))
print("Repo root:", REPO_ROOT)

# COMMAND ----------

from radar.config import load_config  # noqa: E402
from radar.landing import LandingWriter  # noqa: E402
from radar.sample_data import write_sample_data  # noqa: E402

catalog = dbutils.widgets.get("catalog")  # noqa: F821
schema = dbutils.widgets.get("schema")  # noqa: F821
volume = f"/Volumes/{catalog}/{schema}/landing"
if not os.path.isdir(volume):
    raise RuntimeError(f"{volume} does not exist. Run `databricks bundle deploy` first.")

cfg = load_config(os.path.join(REPO_ROOT, "config", "radar.toml"))
counts = write_sample_data(cfg, LandingWriter(volume), days=int(dbutils.widgets.get("days")))  # noqa: F821
display(spark.createDataFrame(sorted(counts.items()), "dataset STRING, records INT"))  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC Next: open **Jobs & Pipelines → rituals-radar-refresh → Run now** (or `databricks bundle run rituals_radar_refresh`),
# MAGIC then open the **Rituals Radar** dashboard.
