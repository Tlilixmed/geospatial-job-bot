"""URL normalisation and classification."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "gh_src", "lever-source", "lever-origin", "source", "src", "ref", "referrer", "referer",
    "trk", "trackingid", "refid", "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "_hsenc",
    "_hsmi", "iis", "iisn", "ccuid", "jobpipeline", "sourcetype", "campaign", "medium",
    "share", "shared", "codes", "ashby_jid_source",
}
TRACKING_PREFIXES = ("utm_", "_ga", "hsa_", "pk_")

AGGREGATOR_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
    "simplyhired.com", "careerbuilder.com", "remotive.com", "remoteok.com", "himalayas.app",
    "jobicy.com", "arbeitnow.com", "talent.com", "jooble.org", "adzuna.com", "google.com",
    "weworkremotely.com", "builtin.com", "wellfound.com", "dice.com", "usajobs.gov",
}

JOBLIKE_PATH_RE = re.compile(
    r"(/jobs?(/|$|\?)|/careers?/|/vacanc|/positions?/|/openings?/|/postings?/|/job-|/job_|/requisition|"
    r"/opportunit|/stellen|/emploi|/offres?/|/empleo|/vagas?/|/recruit|gh_jid=|/apply|jobid=|job_id=)",
    re.IGNORECASE,
)


def is_http_url(url) -> bool:
    if not url or not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def canonicalize_url(url) -> str | None:
    """Stable URL form for identity and deduplication (never used for display)."""
    if not is_http_url(url):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def host_of(url) -> str:
    if not url:
        return ""
    try:
        host = (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host: str) -> str:
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"co", "com", "gov", "org", "ac", "net", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_aggregator(url) -> bool:
    if not url:
        return False
    return registrable_domain(host_of(url)) in AGGREGATOR_DOMAINS


def absolutize(base: str, href) -> str | None:
    if not href or not isinstance(href, str):
        return None
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    try:
        joined = urljoin(base, href)
    except ValueError:
        return None
    return joined.split("#")[0] if is_http_url(joined) else None


def looks_like_job_url(url) -> bool:
    return bool(url) and bool(JOBLIKE_PATH_RE.search(str(url)))
