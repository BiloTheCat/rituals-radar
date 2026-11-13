"""Write collected records as JSON Lines into the landing zone.

Three targets are supported, chosen automatically:

* ``files_api`` – a Unity Catalog volume path (``/Volumes/...``) written from
  outside Databricks (GitHub Actions, your laptop) via the Databricks SDK.
  Needs DATABRICKS_HOST + DATABRICKS_TOKEN (or another SDK auth method).
* ``fuse``      – the same volume path, but we're running *inside* Databricks,
  where volumes are mounted as normal folders.
* ``local``     – any other path: a local folder, handy for tests and dry runs.

File layout (one file per dataset per run, never overwritten):
    <root>/<dataset>/<YYYY-MM-DD>/<dataset>_<YYYYMMDDTHHMMSSZ>_<run_id>.jsonl
"""

from __future__ import annotations

import io
import json
import logging
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


def _volume_root(path: str) -> str | None:
    """'/Volumes/cat/schema/vol/a/b' -> '/Volumes/cat/schema/vol' (None if not a volume path)."""
    parts = path.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "Volumes":
        return "/" + "/".join(parts[:4])
    return None


class LandingWriter:
    def __init__(self, target: str):
        if not target:
            raise ValueError("No landing target configured (landing.volume_path or --out)")
        self.target = target.rstrip("/")
        volume_root = _volume_root(self.target)
        if volume_root is None:
            self.mode = "local"
        elif os.path.isdir(volume_root):
            self.mode = "fuse"  # running inside Databricks
        else:
            self.mode = "files_api"
        self._client = None
        log.info("Landing target: %s (mode=%s)", self.target, self.mode)

    # -- public -----------------------------------------------------------
    def write(
        self,
        dataset: str,
        records: Sequence[dict],
        *,
        run_id: str,
        when: datetime,
        subfolder: str | None = None,
    ) -> str | None:
        """Write `records` to one new file. Returns its path (None when nothing to write)."""
        if not records:
            log.info("No %s records to write", subfolder or dataset)
            return None
        when = when.astimezone(timezone.utc)
        name = subfolder or dataset
        relative = "/".join(
            p for p in (dataset, subfolder, when.strftime("%Y-%m-%d"),
                        f"{name}_{when:%Y%m%dT%H%M%SZ}_{run_id}.jsonl") if p
        )
        payload = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records)
        data = payload.encode("utf-8")
        path = f"{self.target}/{relative}"

        if self.mode == "files_api":
            self._upload(path, data)
        else:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.mode == "fuse":
                target.write_bytes(data)  # volume files become visible on close
            else:
                tmp = target.with_suffix(".jsonl.tmp")
                tmp.write_bytes(data)
                tmp.replace(target)  # atomic: readers never see half a file
        log.info("Wrote %d records -> %s", len(records), path)
        return path

    # -- internals --------------------------------------------------------
    def _upload(self, path: str, data: bytes) -> None:
        if self._client is None:
            try:
                from databricks.sdk import WorkspaceClient
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("pip install databricks-sdk to upload to a volume") from exc
            if not os.environ.get("DATABRICKS_HOST"):
                log.warning("DATABRICKS_HOST is not set; relying on the default SDK auth profile")
            self._client = WorkspaceClient()
        directory = path.rsplit("/", 1)[0]
        self._client.files.create_directory(directory)  # idempotent, like mkdir -p
        self._client.files.upload(path, io.BytesIO(data), overwrite=False)
