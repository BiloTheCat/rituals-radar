"""Centre point of every Dutch 4-digit postcode area (PC4).

Source: CBS "postcode4" statistics, served by PDOK as an open OGC API
(licence CC BY 4.0, attribution: CBS / PDOK). The features are the area
boundaries; we compute each area's centre ourselves so the dashboard can
measure the distance from a postcode to the nearest Rituals stores.

About 4,000 areas, fetched in pages of 1,000. Boundaries barely change, so a
monthly run is plenty.
"""

from __future__ import annotations

import logging
from datetime import datetime

from radar.http_client import PoliteSession
from radar.records import PC4_FIELDS, conform
from radar.util import iso

log = logging.getLogger(__name__)

ITEMS_URL = "https://api.pdok.nl/cbs/postcode4/ogc/v1/collections/postcode4/items"
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"  # plain longitude/latitude
MAX_PAGES = 20


def _ring_centroid(ring: list) -> tuple[float, float, float]:
    """(signed area, x, y) of one polygon ring via the shoelace formula."""
    area = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    area /= 2.0
    if area == 0:
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return area, cx / (6 * area), cy / (6 * area)


def centroid(geometry: dict) -> tuple[float, float] | None:
    """(longitude, latitude) of a Polygon/MultiPolygon, weighted by the area of its outer rings.

    Planar maths on degrees is fine at the size of a postcode area.
    """
    if not geometry:
        return None
    if geometry.get("type") == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry.get("type") == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        return None
    total = sx = sy = 0.0
    for polygon in polygons:
        if not polygon or len(polygon[0]) < 3:
            continue
        area, x, y = _ring_centroid([tuple(p[:2]) for p in polygon[0]])
        weight = abs(area) or 1e-12
        total += weight
        sx += x * weight
        sy += y * weight
    if total == 0:
        return None
    return sx / total, sy / total


def collect_postcodes(session: PoliteSession, *, now: datetime, run_id: str) -> list[dict]:
    """PC4 centres for the most recent CBS year that has data."""
    for year in range(now.year - 1, now.year - 5, -1):
        records = _fetch_year(session, year, now=now, run_id=run_id)
        if records:
            log.info("Postcodes: %d PC4 areas (CBS %d)", len(records), year)
            return records
        log.info("No CBS postcode4 data for %d, trying the year before", year)
    raise RuntimeError("No CBS postcode4 data found for the last four years")


def _fetch_year(session: PoliteSession, year: int, *, now: datetime, run_id: str) -> list[dict]:
    records: dict[str, dict] = {}
    url = ITEMS_URL
    params: dict | None = {"f": "json", "limit": 1000, "jaarcode": year, "crs": CRS84}
    for _ in range(MAX_PAGES):
        response = session.get(url, params=params, timeout=120)
        response.raise_for_status()
        data = response.json()
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            code = props.get("postcode")
            point = centroid(feature.get("geometry") or {})
            if code is None or point is None:
                continue
            pc4 = str(code).zfill(4)
            records[pc4] = conform({
                "run_id": run_id,
                "fetched_at": iso(now),
                "pc4": pc4,
                "latitude": round(point[1], 6),
                "longitude": round(point[0], 6),
                "source_year": year,
                "is_sample": False,
            }, PC4_FIELDS)
        next_link = next((link["href"] for link in data.get("links", []) if link.get("rel") == "next"), None)
        if not next_link:
            break
        url, params = next_link, None  # the next link already carries all parameters
    return list(records.values())
