"""Rituals Radar collectors.

Small, dependency-light Python package that fetches public Rituals product pages
and the sitemap, and lands the results as JSON Lines in a Databricks Unity
Catalog volume (or a local folder). Everything downstream happens in Databricks.
"""

__version__ = "1.0.0"
