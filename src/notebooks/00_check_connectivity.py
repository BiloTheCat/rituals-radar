# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Can this workspace reach the internet?
# MAGIC
# MAGIC Databricks **Free Edition** only allows outbound traffic to a list of trusted domains
# MAGIC (the list differs per region). That's why the collectors run in **GitHub Actions** by default.
# MAGIC
# MAGIC Run this notebook to see what *your* workspace can reach. If rituals.com says `reachable`
# MAGIC (for example after the optional LinkedIn verification, which can unlock outbound access),
# MAGIC you can also run the collectors inside Databricks: see `03_run_collectors` and
# MAGIC `resources/optional/collectors_in_databricks.yml`.

# COMMAND ----------

import requests

TARGETS = {
    "PyPI (always allowed)": "https://pypi.org/simple/requests/",
    "rituals.com (stock + sitemap)": "https://www.rituals.com/robots.txt",
    "PDOK (postcode areas)": "https://api.pdok.nl/cbs/postcode4/ogc/v1/collections?f=json",
}

results = []
for name, url in TARGETS.items():
    try:
        response = requests.get(url, timeout=10, headers={"User-Agent": "RitualsRadar connectivity check"})
        # Any HTTP answer means the network path is open.
        results.append((name, "reachable", f"HTTP {response.status_code}"))
    except requests.RequestException as exc:
        results.append((name, "BLOCKED", type(exc).__name__))

display(spark.createDataFrame(results, "target STRING, status STRING, detail STRING"))  # noqa: F821
