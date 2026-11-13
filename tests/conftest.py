"""Shared test helpers: the real config, a matcher and a fake HTTP session (no network)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

from radar.config import load_config
from radar.matching import matcher_from_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)

    def json(self):
        return self._payload if self._payload is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    """Stands in for radar.http_client.PoliteSession.

    `routes` maps a URL prefix to a FakeResponse, an exception instance, or a
    callable(url, params) returning one of those.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def _handle(self, method, url, params=None, **kwargs):
        self.calls.append((method, url, dict(params or {})))
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                result = self.routes[prefix]
                if callable(result) and not isinstance(result, FakeResponse):
                    result = result(url, params or {})
                if isinstance(result, Exception):
                    raise result
                return result
        return FakeResponse(404, "not found")

    def get(self, url, params=None, **kwargs):
        return self._handle("GET", url, params, **kwargs)

    def post(self, url, params=None, **kwargs):
        return self._handle("POST", url, params, **kwargs)


@pytest.fixture(scope="session")
def cfg():
    return load_config(REPO_ROOT / "config" / "radar.toml")


@pytest.fixture(scope="session")
def matcher(cfg):
    return matcher_from_config(cfg)
