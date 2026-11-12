# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Run the collectors inside Databricks (optional)
# MAGIC
# MAGIC Only useful when `00_check_connectivity` shows that your workspace can reach the sites.
# MAGIC By default the collectors run in GitHub Actions instead.
# MAGIC
# MAGIC API keys are read from a Databricks **secret scope** called `rituals_radar`
# MAGIC (create it with the CLI, see docs/HANDLEIDING.md). Missing keys simply skip that source.

# COMMAND ----------

dbutils.widgets.dropdown("command", "stock", ["stock", "catalog", "social", "daily", "all"], "Command")  # noqa: F821

# COMMAND ----------

import os
import sys

SECRET_SCOPE = "rituals_radar"
for key in ("YOUTUBE_API_KEY", "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD", "X_BEARER_TOKEN", "RADAR_HASH_SALT"):
    try:
        os.environ[key] = dbutils.secrets.get(SECRET_SCOPE, key)  # noqa: F821
    except Exception:  # noqa: BLE001 - secret (or scope) not configured
        os.environ.pop(key, None)


def find_repo_root() -> str:
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

# COMMAND ----------

from radar.cli import main  # noqa: E402

exit_code = main([dbutils.widgets.get("command"), "--config", os.path.join(REPO_ROOT, "config", "radar.toml")])  # noqa: F821
if exit_code != 0:
    raise RuntimeError(f"Collector run failed (exit code {exit_code}); see the log output above")
