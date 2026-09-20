"""robots.txt handling built on urllib.robotparser.

Behaviour follows RFC 9309:
* 2xx  -> parse and obey
* 4xx  -> no restrictions ("unavailable")
* 5xx / network failure -> treat as full disallow for this run ("unreachable")
Decisions are cached per origin for the lifetime of the run. Crawl-delay is applied to the
client's per-host delay (capped at 30 seconds).
"""
from __future__ import annotations

import logging
import threading
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)


class RobotsCache:
    def __init__(self, client, agent_token: str, *, max_crawl_delay: float = 30.0):
        self.client = client
        self.agent_token = agent_token
        self.max_crawl_delay = max_crawl_delay
        self._parsers: dict[str, RobotFileParser | str] = {}
        self._lock = threading.Lock()
        self._origin_locks: dict[str, threading.Lock] = {}

    def _origin(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        origin = f"{parts.scheme}://{parts.netloc}".lower()
        return origin, host

    def _load(self, origin: str, host: str) -> RobotFileParser | str:
        from .http import FetchError  # local import to avoid cycle

        robots_url = f"{origin}/robots.txt"
        try:
            response = self.client.request(
                "GET", robots_url, respect_robots=False, timeout=20, detect_challenge=False, max_bytes=512 * 1024
            )
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.kind not in ("RATE_LIMITED",):
                return "ALLOW_ALL"
            log.info("robots.txt unreachable for %s (%s): treating as disallow", origin, exc.kind)
            return "DISALLOW_ALL"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            text = response.content.decode("utf-8-sig", "replace")  # a BOM before "User-agent" hides the first group
        except Exception:
            text = ""
        parser.parse(text.splitlines())
        delay = parser.crawl_delay(self.agent_token)
        if delay:
            try:
                self.client.set_min_delay(host, min(float(delay), self.max_crawl_delay))
            except (TypeError, ValueError):
                pass
        return parser

    def _parser_for(self, url: str) -> RobotFileParser | str:
        origin, host = self._origin(url)
        with self._lock:
            if origin in self._parsers:
                return self._parsers[origin]
            lock = self._origin_locks.setdefault(origin, threading.Lock())
        with lock:
            with self._lock:
                if origin in self._parsers:
                    return self._parsers[origin]
            parser = self._load(origin, host)
            with self._lock:
                self._parsers[origin] = parser
            return parser

    def allowed(self, url: str) -> bool:
        parser = self._parser_for(url)
        if parser == "ALLOW_ALL":
            return True
        if parser == "DISALLOW_ALL":
            return False
        return parser.can_fetch(self.agent_token, url)

    def sitemaps(self, url: str) -> list[str]:
        parser = self._parser_for(url)
        if isinstance(parser, str):
            return []
        return list(parser.site_maps() or [])
