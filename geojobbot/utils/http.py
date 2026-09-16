"""Polite HTTP client.

* descriptive User-Agent (no browser impersonation)
* per-host minimum delay, doubled after HTTP 429
* at most 3 retries (2s, 4s, 8s) for 429/5xx/timeouts/connection errors, honouring Retry-After
* robots.txt enforcement for crawled pages (see robots.py)
* challenge/CAPTCHA pages are reported as BLOCKED and never circumvented
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

import requests

from .dates import utcnow

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
CHALLENGE_MARKERS = (
    "g-recaptcha", "h-captcha", "hcaptcha.com", "cf-challenge", "challenge-platform", "cf_chl_",
    "please verify you are a human", "are you a robot", "captcha-delivery", "px-captcha",
)


class FetchError(Exception):
    """A failed fetch. ``kind`` is a stable machine-readable category."""

    def __init__(self, kind: str, message: str = "", status: int | None = None, url: str | None = None):
        self.kind = kind
        self.status = status
        self.url = url
        super().__init__(f"{kind}{f' {status}' if status else ''}: {message}".strip())


@dataclass
class HttpStats:
    requests: int = 0
    retries: int = 0
    errors: Counter = field(default_factory=Counter)
    status_codes: Counter = field(default_factory=Counter)
    robots_blocked: int = 0

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "errors": dict(self.errors),
            "status_codes": {str(k): v for k, v in self.status_codes.items()},
            "robots_blocked": self.robots_blocked,
        }


class HttpClient:
    def __init__(
        self,
        user_agent: str,
        *,
        default_delay: float = 1.5,
        host_delays: dict[str, float] | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        max_retry_after: float = 60.0,
        max_bytes: int = 15 * 1024 * 1024,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        robots=None,
    ):
        self.user_agent = user_agent
        self.default_delay = default_delay
        self.host_delays = dict(host_delays or {})
        self.timeout = timeout
        self.max_retries = min(max_retries, 3)
        self.backoff_base = backoff_base
        self.max_retry_after = max_retry_after
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self.sleep = sleep
        self.clock = clock
        self.robots = robots
        self.stats = HttpStats()
        self._host_next: dict[str, float] = {}
        self._host_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ rate limiting
    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Lock()
            return self._host_locks[host]

    def delay_for(self, host: str) -> float:
        for suffix, delay in self.host_delays.items():
            if host == suffix or host.endswith("." + suffix):
                return delay
        return self.default_delay

    def set_min_delay(self, host: str, delay: float) -> None:
        with self._lock:
            self.host_delays[host] = max(self.host_delays.get(host, self.default_delay), delay)

    def _wait_turn(self, host: str) -> None:
        lock = self._host_lock(host)
        with lock:
            now = self.clock()
            ready = self._host_next.get(host, 0.0)
            if ready > now:
                self.sleep(ready - now)
            self._host_next[host] = max(self.clock(), ready) + self.delay_for(host)

    def _penalize(self, host: str, seconds: float) -> None:
        with self._host_lock(host):
            self._host_next[host] = max(self._host_next.get(host, 0.0), self.clock() + seconds)

    # ------------------------------------------------------------------ helpers
    def _retry_after(self, response) -> float | None:
        value = response.headers.get("Retry-After") if response is not None else None
        if not value:
            return None
        value = value.strip()
        if value.isdigit():
            return float(value)
        try:
            when = parsedate_to_datetime(value)
            return max(0.0, (when - utcnow()).total_seconds())
        except (TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _looks_like_challenge(response) -> bool:
        ctype = response.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            return False
        body = response.content[:60000].decode("utf-8", "ignore").lower()
        return any(marker in body for marker in CHALLENGE_MARKERS) and len(body) < 60000

    # ------------------------------------------------------------------ main entry
    def request(
        self,
        method: str,
        url: str,
        *,
        params=None,
        json=None,
        data=None,
        headers: dict | None = None,
        respect_robots: bool = True,
        timeout: float | None = None,
        max_bytes: int | None = None,
        expect_json: bool = False,
        detect_challenge: bool = True,
    ) -> requests.Response:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError as exc:
            raise FetchError("INVALID_URL", str(exc), url=url) from exc
        if not host:
            raise FetchError("INVALID_URL", "missing host", url=url)
        if respect_robots and self.robots is not None and not self.robots.allowed(url):
            self.stats.robots_blocked += 1
            raise FetchError("ROBOTS_DISALLOWED", "blocked by robots.txt", url=url)

        req_headers = {"User-Agent": self.user_agent, "Accept-Language": "en;q=0.9,*;q=0.5"}
        if expect_json:
            req_headers["Accept"] = "application/json"
        if headers:
            req_headers.update(headers)

        attempt = 0
        while True:
            self._wait_turn(host)
            self.stats.requests += 1
            response = None
            error: FetchError | None = None
            try:
                response = self.session.request(
                    method, url, params=params, json=json, data=data, headers=req_headers,
                    timeout=timeout or self.timeout, allow_redirects=True,
                )
            except requests.exceptions.Timeout as exc:
                error = FetchError("TIMEOUT", type(exc).__name__, url=url)
            except requests.exceptions.ConnectionError as exc:
                error = FetchError("CONNECTION_ERROR", type(exc).__name__, url=url)
            except requests.exceptions.RequestException as exc:
                self.stats.errors["REQUEST_ERROR"] += 1
                raise FetchError("REQUEST_ERROR", type(exc).__name__, url=url) from exc

            retry_wait = None
            if response is not None:
                status = response.status_code
                self.stats.status_codes[status] += 1
                if status < 400:
                    limit = max_bytes or self.max_bytes
                    if len(response.content) > limit:
                        self.stats.errors["TOO_LARGE"] += 1
                        raise FetchError("TOO_LARGE", f"{len(response.content)} bytes", status, url)
                    if detect_challenge and self._looks_like_challenge(response):
                        self.stats.errors["BLOCKED"] += 1
                        raise FetchError("BLOCKED", "challenge/CAPTCHA page (not bypassed)", status, url)
                    return response
                if status in RETRYABLE_STATUS:
                    kind = "RATE_LIMITED" if status == 429 else "SERVER_ERROR"
                    error = FetchError(kind, response.reason or "", status, url)
                    retry_wait = self._retry_after(response)
                    if status == 429:
                        self.set_min_delay(host, min(self.delay_for(host) * 2, 30.0))
                else:
                    kind = {401: "AUTH_REQUIRED", 403: "BLOCKED", 404: "NOT_FOUND", 410: "NOT_FOUND"}.get(
                        status, "HTTP_ERROR"
                    )
                    self.stats.errors[kind] += 1
                    raise FetchError(kind, response.reason or "", status, url)

            assert error is not None
            if attempt >= self.max_retries:
                self.stats.errors[error.kind] += 1
                raise error
            wait = self.backoff_base * (2 ** attempt)
            if retry_wait is not None:
                if retry_wait > self.max_retry_after:
                    self.stats.errors[error.kind] += 1
                    raise FetchError(error.kind, f"Retry-After {retry_wait:.0f}s exceeds limit", error.status, url)
                wait = max(wait, retry_wait)
            attempt += 1
            self.stats.retries += 1
            log.debug("retry %s %s in %.1fs (%s)", method, url, wait, error.kind)
            self.sleep(wait)

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def get_json(self, url: str, **kwargs):
        response = self.get(url, expect_json=True, detect_challenge=False, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            self.stats.errors["BAD_JSON"] += 1
            raise FetchError("BAD_JSON", "response was not valid JSON", response.status_code, url) from exc
