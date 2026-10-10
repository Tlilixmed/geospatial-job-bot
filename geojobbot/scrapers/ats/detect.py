"""Recognise ATS-hosted URLs, extracting board references and globally stable native job IDs.

Native IDs make the same job discovered via an ATS API, a company page, a search result or an
aggregator collapse into one canonical record.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

RESERVED_SLUGS = {
    "embed", "api", "v0", "v1", "jobs", "job", "static", "assets", "favicon.ico", "robots.txt", "sitemap.xml",
    "privacy", "terms", "login", "signin", "signup", "search", "about", "blog", "help", "support", "careers",
    "", "www", "app", "cdn", "images", "img", "css", "js", "fonts", "public", "home", "en", "de", "fr", "es",
    "static-assets", "boards", "posting-api", "job-board", "companies", "oauth", "sso",
}
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


@dataclass(frozen=True)
class BoardRef:
    ats: str
    slug: str  # for workday: "tenant/wdN/site", for oracle: "pod.dc/site"

    @property
    def key(self) -> str:
        return f"{self.ats}:{self.slug.lower()}"


def _valid_slug(slug: str | None) -> bool:
    return bool(slug) and slug.lower() not in RESERVED_SLUGS and bool(SLUG_RE.match(slug))


def parse_workday_url(url: str) -> BoardRef | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    m = re.match(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$", host)
    if not m:
        return None
    tenant, wd = m.group(1), m.group(2)
    segments = [s for s in parts.path.split("/") if s]
    if segments and segments[0] == "wday":  # API URL: /wday/cxs/{tenant}/{site}/...
        if len(segments) >= 4 and segments[1] == "cxs":
            return BoardRef("workday", f"{tenant}/{wd}/{segments[3]}")
        return None
    if segments and re.fullmatch(r"[a-z]{2}(-[A-Za-z]{2})?", segments[0]):
        segments = segments[1:]
    if not segments or segments[0].lower() in {"job", "jobs", "login", "userhome"}:
        return None
    site = segments[0]
    # Workday site names are chosen by the tenant; "Careers" or "Search" are common and legitimate,
    # so only the generic Workday paths excluded above are reserved here, not RESERVED_SLUGS.
    if not SLUG_RE.match(site):
        return None
    return BoardRef("workday", f"{tenant}/{wd}/{site}")


ORACLE_HOST_RE = re.compile(r"^([a-z0-9-]+)\.fa\.([a-z0-9-]+)\.oraclecloud\.com$")
# /hcmUI/CandidateExperience/{lang}/sites/{site}[/job/{Id} | /requisitions/preview/{Id}]
ORACLE_PAGE_RE = re.compile(r"^/hcmUI/CandidateExperience/[A-Za-z]{2}(?:[-_][A-Za-z]{2,4})?/sites/([A-Za-z0-9_]+)"
                            r"(?:/(?:requisitions/)?(?:job|preview)/(\d+))?")


def _oracle(parts) -> tuple[str | None, BoardRef | None]:
    """Oracle Recruiting Cloud: the board is "{pod}.{dc}/{site}" ("emit.ca3/CX_2001"), the native id
    "oracle:{pod}.{dc}:{Id}" (requisition ids are unique across a tenant's sites)."""
    host = ORACLE_HOST_RE.match((parts.hostname or "").lower())
    if not host:
        return None, None
    tenant = f"{host.group(1)}.{host.group(2)}"
    site = job_id = None
    page = ORACLE_PAGE_RE.match(parts.path)
    if page:
        site, job_id = page.group(1), page.group(2)
    elif "/hcmRestApi/" in parts.path and "/recruitingCEJobRequisition" in parts.path:
        # API: ...recruitingCEJobRequisitions?finder=findReqs;siteNumber=CX_1,... or ...Details?finder=ById;Id="9",siteNumber=CX_1
        finder = (parse_qs(parts.query).get("finder") or [""])[0]
        site_m = re.search(r"\bsiteNumber=([A-Za-z0-9_]+)", finder)
        id_m = re.search(r'\bId="?(\d+)', finder)
        site, job_id = site_m.group(1) if site_m else None, id_m.group(1) if id_m else None
    return (f"oracle:{tenant}:{job_id}" if job_id else None), (BoardRef("oracle", f"{tenant}/{site}") if site else None)


def parse_oracle_url(url: str) -> BoardRef | None:
    try:
        return _oracle(urlsplit(url.strip()))[1]
    except ValueError:
        return None


def parse_board_url(ats: str, value: str) -> BoardRef | None:
    """A board as written in sources.toml: Workday and Oracle take the career-site URL, the others the slug."""
    if ats == "workday":
        return parse_workday_url(value)
    if ats == "oracle":
        return parse_oracle_url(value)
    return BoardRef(ats, value)


def detect(url: str | None) -> tuple[str | None, BoardRef | None]:
    """Return (native_job_id, board_ref) for an ATS URL; (None, None) if not recognised."""
    if not url or not isinstance(url, str):
        return None, None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None, None
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    query = parse_qs(parts.query)

    native = None
    board = None

    gh_jid = (query.get("gh_jid") or [None])[0]
    if gh_jid and gh_jid.isdigit():
        native = f"greenhouse:{gh_jid}"

    if host.endswith("greenhouse.io"):
        if host.startswith("boards-api."):
            # /v1/boards/{slug}/jobs/{id}
            if len(segments) >= 3 and segments[0] == "v1" and segments[1] == "boards" and _valid_slug(segments[2]):
                board = BoardRef("greenhouse", segments[2])
                if len(segments) >= 5 and segments[3] == "jobs" and segments[4].isdigit():
                    native = f"greenhouse:{segments[4]}"
        elif segments[:1] == ["embed"]:
            slug = (query.get("for") or [None])[0]
            token = (query.get("token") or [None])[0]
            if _valid_slug(slug):
                board = BoardRef("greenhouse", slug)
            if token and token.isdigit():
                native = f"greenhouse:{token}"
        elif segments and _valid_slug(segments[0]):
            board = BoardRef("greenhouse", segments[0])
            if len(segments) >= 3 and segments[1] == "jobs" and segments[2].isdigit():
                native = f"greenhouse:{segments[2]}"
    elif host in ("jobs.lever.co", "jobs.eu.lever.co"):
        if segments and _valid_slug(segments[0]):
            board = BoardRef("lever", segments[0])
            if len(segments) >= 2 and re.fullmatch(UUID_RE, segments[1]):
                native = f"lever:{segments[1].lower()}"
    elif host in ("api.lever.co", "api.eu.lever.co"):
        if len(segments) >= 3 and segments[:2] == ["v0", "postings"] and _valid_slug(segments[2]):
            board = BoardRef("lever", segments[2])
            if len(segments) >= 4 and re.fullmatch(UUID_RE, segments[3]):
                native = f"lever:{segments[3].lower()}"
    elif host == "jobs.ashbyhq.com":
        if segments and _valid_slug(segments[0]):
            board = BoardRef("ashby", segments[0])
            if len(segments) >= 2 and re.fullmatch(UUID_RE, segments[1]):
                native = f"ashby:{segments[1].lower()}"
    elif host == "api.ashbyhq.com":
        if len(segments) >= 3 and segments[:2] == ["posting-api", "job-board"] and _valid_slug(segments[2]):
            board = BoardRef("ashby", segments[2])
    elif host == "apply.workable.com":
        if segments and segments[0] == "api":
            if len(segments) >= 5 and segments[3] == "accounts" and _valid_slug(segments[4]):
                board = BoardRef("workable", segments[4])
        elif segments and _valid_slug(segments[0]):
            board = BoardRef("workable", segments[0])
            if len(segments) >= 3 and segments[1] == "j" and re.fullmatch(r"[A-Za-z0-9]{6,12}", segments[2]):
                native = f"workable:{segments[2].upper()}"
    elif host == "jobs.smartrecruiters.com" or host == "careers.smartrecruiters.com":
        if segments and _valid_slug(segments[0]):
            board = BoardRef("smartrecruiters", segments[0])
            if len(segments) >= 2:
                m = re.match(r"^(\d{6,})", segments[1])
                if m:
                    native = f"smartrecruiters:{m.group(1)}"
    elif host == "api.smartrecruiters.com":
        if len(segments) >= 3 and segments[:2] == ["v1", "companies"] and _valid_slug(segments[2]):
            board = BoardRef("smartrecruiters", segments[2])
            if len(segments) >= 5 and segments[3] == "postings" and segments[4].isdigit():
                native = f"smartrecruiters:{segments[4]}"
    elif host.endswith(".recruitee.com"):
        slug = host[: -len(".recruitee.com")]
        if _valid_slug(slug) and "." not in slug:
            board = BoardRef("recruitee", slug)
            if len(segments) >= 2 and segments[0] == "o" and _valid_slug(segments[1]):
                native = f"recruitee:{slug.lower()}:{segments[1].lower()}"
    elif re.search(r"\.jobs\.personio\.(de|com)$", host):
        slug = host.split(".jobs.personio.")[0]
        if _valid_slug(slug):
            board = BoardRef("personio", slug)
            if len(segments) >= 2 and segments[0] == "job" and segments[1].isdigit():
                native = f"personio:{slug.lower()}:{segments[1]}"
    elif host.endswith(".myworkdayjobs.com"):
        board = parse_workday_url(url)
        if board is not None:
            m = re.search(r"_([A-Za-z0-9-]*\d[A-Za-z0-9-]*)(?:-\d+)?/?$", parts.path)
            if "/job/" in parts.path and m:
                tenant = board.slug.split("/")[0]
                native = f"workday:{tenant.lower()}:{m.group(1).upper()}"
    elif host.endswith(".oraclecloud.com"):
        native, board = _oracle(parts)
    return native, board


ATS_URL_RE = re.compile(
    r"https?://(?:"
    r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/[^\s\"'<>)]+|"
    r"boards-api\.greenhouse\.io/v1/boards/[^\s\"'<>)]+|"
    r"jobs(?:\.eu)?\.lever\.co/[^\s\"'<>)]+|"
    r"jobs\.ashbyhq\.com/[^\s\"'<>)]+|"
    r"apply\.workable\.com/[^\s\"'<>)]+|"
    r"(?:jobs|careers)\.smartrecruiters\.com/[^\s\"'<>)]+|"
    r"[a-z0-9-]+\.recruitee\.com[^\s\"'<>)]*|"
    r"[a-z0-9-]+\.jobs\.personio\.(?:de|com)[^\s\"'<>)]*|"
    r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>)]+|"
    r"[a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com/[^\s\"'<>)]+"
    r")",
    re.IGNORECASE,
)


def find_boards_in_text(text: str, limit: int = 50) -> list[BoardRef]:
    """Find ATS board references embedded in arbitrary HTML/JS (career page embeds, links)."""
    found: dict[str, BoardRef] = {}
    for m in ATS_URL_RE.finditer(text or ""):
        url = m.group(0).replace("&amp;", "&").rstrip(".,;\\")
        _, board = detect(url)
        if board and board.key not in found:
            found[board.key] = board
            if len(found) >= limit:
                break
    return list(found.values())
