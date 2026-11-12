"""A polite HTTP client: honest User-Agent, retries with back-off, a fixed delay
between requests and (for websites) a robots.txt check before every fetch."""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class BlockedByRobots(Exception):
    """Raised when robots.txt disallows a URL for our user agent."""


class PoliteSession:
    def __init__(
        self,
        user_agent: str,
        *,
        delay_seconds: float = 0.5,
        timeout_seconds: float = 20.0,
        respect_robots_txt: bool = False,
        max_retries: int = 3,
        accept_language: str = "nl-NL,nl;q=0.9,en;q=0.8",
    ):
        self.delay_seconds = delay_seconds
        self.timeout_seconds = timeout_seconds
        self.respect_robots_txt = respect_robots_txt
        self.user_agent = user_agent
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

        retry = Retry(
            total=max_retries,
            backoff_factor=2.0,  # 2s, 4s, 8s ...
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": accept_language})

    # ------------------------------------------------------------------
    def get(self, url: str, **kwargs) -> requests.Response:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._request("POST", url, **kwargs)

    # ------------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if self.respect_robots_txt and not self.allowed_by_robots(url):
            raise BlockedByRobots(url)
        self._throttle(url)
        kwargs.setdefault("timeout", self.timeout_seconds)
        return self.session.request(method, url, **kwargs)

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = self.delay_seconds - (time.monotonic() - last) + random.uniform(0, 0.3 * self.delay_seconds)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        if parser is None:  # robots.txt could not be read -> play safe
            return False
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        robots_url = f"{origin}/robots.txt"
        try:
            self._throttle(robots_url)
            response = self.session.get(robots_url, timeout=self.timeout_seconds)
        except requests.RequestException as exc:
            log.warning("Could not fetch %s (%s); skipping this site for now", robots_url, exc)
            return None
        parser = RobotFileParser(robots_url)
        if response.status_code >= 500:
            log.warning("%s returned %s; skipping this site for now", robots_url, response.status_code)
            return None
        if response.status_code >= 400:
            parser.parse([])  # no robots.txt = everything allowed
        else:
            parser.parse(response.text.splitlines())
        return parser
