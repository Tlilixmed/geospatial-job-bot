"""Shared offline test doubles: fake HTTP session, fake S3 client, settings factory."""
from __future__ import annotations

import io
import json as jsonlib
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from geojobbot.config import Settings  # noqa: E402

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, url="", reason=""):
        if isinstance(body, (dict, list)):
            body = jsonlib.dumps(body).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        elif isinstance(body, str):
            body = body.encode()
        self.status_code = status
        self.content = body
        self.headers = requests.structures.CaseInsensitiveDict(headers or {"Content-Type": "text/html"})
        self.url = url
        self.reason = reason or ("OK" if status < 400 else "Error")

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def json(self):
        return jsonlib.loads(self.content.decode())


class FakeSession:
    """Routes requests to handlers. routes: {prefix: response | [responses] | callable(method,url,params,json)}.

    robots.txt returns 404 (allow all) unless routed explicitly.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def request(self, method, url, params=None, json=None, data=None, headers=None, timeout=None, allow_redirects=True):
        full = url + ("?" + urlencode(params) if params else "")
        self.calls.append((method, full, json))
        best = None
        for prefix in self.routes:
            if full.startswith(prefix) and (best is None or len(prefix) > len(best)):
                best = prefix
        if best is None:
            if url.endswith("/robots.txt"):
                return FakeResponse(404, b"", url=url)
            return FakeResponse(404, b"not routed", url=url)
        handler = self.routes[best]
        if callable(handler) and not isinstance(handler, FakeResponse):
            result = handler(method, full, params, json)
        elif isinstance(handler, list):
            result = handler.pop(0) if len(handler) > 1 else handler[0]
        else:
            result = handler
        if isinstance(result, Exception):
            raise result
        if not result.url:
            result.url = url
        return result

    def post(self, url, json=None, timeout=None, **kw):
        return self.request("POST", url, json=json, timeout=timeout)


class FakeClientError(Exception):
    def __init__(self, code, status=400):
        self.response = {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
        super().__init__(code)


class FakeS3:
    """Minimal in-memory S3/R2 client implementing the calls R2Store uses."""

    def __init__(self):
        self.objects = {}
        self.fail = None

    def _check(self):
        if self.fail:
            raise self.fail

    def get_object(self, Bucket, Key):
        self._check()
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self._check()
        import hashlib
        self.objects[Key] = bytes(Body)
        return {"ETag": '"%s"' % hashlib.md5(Body).hexdigest()}

    def head_object(self, Bucket, Key):
        self._check()
        if Key not in self.objects:
            raise FakeClientError("404", 404)
        import hashlib
        return {"ETag": '"%s"' % hashlib.md5(self.objects[Key]).hexdigest(), "ContentLength": len(self.objects[Key])}

    def copy_object(self, Bucket, Key, CopySource):
        self._check()
        self.objects[Key] = self.objects[CopySource["Key"]]

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        self._check()
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]  # tiny pages to exercise pagination
        truncated = start + 2 < len(keys)
        return {"Contents": [{"Key": k} for k in page], "IsTruncated": truncated,
                "NextContinuationToken": str(start + 2) if truncated else None}

    def delete_objects(self, Bucket, Delete):
        self._check()
        for obj in Delete["Objects"]:
            self.objects.pop(obj["Key"], None)
        return {}


def make_settings(**overrides) -> Settings:
    s = Settings()
    s.sources = {}
    s.feeds_enabled = []
    s.jobspy_enabled = False
    s.commoncrawl_enabled = False
    s.duckduckgo_enabled = False
    s.default_host_delay = 0
    s.store_raw_snapshots = True
    s.weekly_summary = False  # enabled explicitly by the tests that cover it
    s.sponsor_registers = False
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


@pytest.fixture
def settings():
    return make_settings()


def make_ctx(session, settings=None, state=None):
    from geojobbot.core.boards import BoardRegistry
    from geojobbot.scrapers.base import RunContext
    from geojobbot.utils.http import HttpClient
    from geojobbot.utils.robots import RobotsCache

    settings = settings or make_settings()
    client = HttpClient("TestBot/1.0", default_delay=0, session=session, sleep=lambda s: None)
    client.robots = RobotsCache(client, "TestBot")
    state = state if state is not None else {"boards": {}, "cursors": {}, "page_cache": {}, "jobs": {}, "sources": {}}
    return RunContext(settings, client, state, run_id="test", now=NOW, registry=BoardRegistry(state, NOW),
                      match_config=settings.match_config())


GIS_DESCRIPTION = (
    "We are hiring a GIS Analyst to support utility mapping projects. Responsibilities include spatial analysis, "
    "digitizing features, georeferencing scanned records and QA/QC of spatial data. Experience with ArcGIS Pro is "
    "required. Python and ArcPy scripting preferred. Familiarity with FME and PostGIS is a plus. You will maintain "
    "the enterprise geodatabase, produce maps and work with LiDAR point cloud data and coordinate systems."
)
