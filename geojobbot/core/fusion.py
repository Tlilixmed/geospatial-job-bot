"""Canonical identity, cross-source grouping and field fusion.

Identity keys per observation (strongest first):
  native ATS id      greenhouse:123 / lever:<uuid> / ...   (also recovered from URLs such as ?gh_jid=)
  source job id      remotive:987
  canonical URL      url:<sha1>
  content hash       hash:<sha1 of company|title|location>
  fuzzy key          fz:<sha1 of normalised company|title|place>  (weak; guarded)

Observations sharing an id are merged. A shared URL merges them too, unless a source both sides know
gave them different ids: two France Travail offers pointing at the same generic "apply here" page are
two jobs. Fuzzy keys merge groups only when that cannot join two different native ATS postings, and
reach back into the state only as far as FUZZY_STATE_MATCH_DAYS (a posting that comes back later is a
repost and is alerted again). Existing state records are matched through their stored aliases so a
job keeps the same canonical id across runs and sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import SOURCE_PRIORITY, RawJob
from datetime import timedelta

from ..utils.dates import parse_datetime, to_iso
from ..utils.location import FULLY_REMOTE_TEXT_RE, ParsedLocation, parse_location
from ..utils.text import fold, normalize_company, normalize_title, sha1
from ..utils.urls import canonicalize_url, is_aggregator

FUZZY_STATE_MATCH_DAYS = 60
# Below this many characters a description is an excerpt (feed summaries run 200-280 chars); the matcher
# uses the same bar to decide whether a job has a description at all.
FULL_DESCRIPTION_CHARS = 300


@dataclass
class FusedJob:
    canonical_id: str
    raws: list[RawJob]
    title: str
    company: str | None
    url: str | None
    apply_url: str | None
    description: str
    location: ParsedLocation
    employment_type: str | None
    salary: str | None
    posted_at: object
    posted_at_reliable: bool
    priority: int
    extraction_method: str
    from_rotation: bool
    board_keys: set = field(default_factory=set)
    aliases: list = field(default_factory=list)
    fuzzy_key: str | None = None

    @property
    def description_hash(self) -> str | None:
        text = fold(self.description)
        return sha1(text) if text else None

    @property
    def content_fingerprint(self) -> str:
        loc = self.location
        return sha1("|".join([normalize_title(self.title), fold(loc.raw), fold(self.salary or ""),
                              str(loc.remote), self.description_hash or ""]))

    def sources(self, now) -> list[dict]:
        seen = {}
        for raw in sorted(self.raws, key=lambda r: -r.priority):
            key = (raw.source_name, canonicalize_url(raw.url or raw.source_url) or raw.source_url)
            if key not in seen:
                seen[key] = {"source_type": raw.source_type, "source_name": raw.source_name,
                             "source_url": raw.source_url, "job_url": raw.url, "method": raw.extraction_method,
                             "first_seen": to_iso(now), "last_seen": to_iso(now)}
        return list(seen.values())


def strong_keys(raw: RawJob) -> list[str]:
    from ..scrapers.ats.detect import detect

    keys = []
    if raw.native_id:
        keys.append(raw.native_id.lower())
    for candidate in (raw.url, raw.apply_url):
        native, _ = detect(candidate)
        if native and native.lower() not in keys:
            keys.append(native.lower())
    if raw.source_job_id:
        sid = raw.source_job_id if ":" in raw.source_job_id else f"{raw.source_name}:{raw.source_job_id}"
        keys.append(sid.lower())
    for candidate in (raw.url, raw.apply_url):
        canon = canonicalize_url(candidate)
        if canon:
            key = f"url:{sha1(canon)}"
            if key not in keys:
                keys.append(key)
    return keys


def fuzzy_key(company: str | None, title: str | None, location: ParsedLocation) -> str | None:
    c, t = normalize_company(company), normalize_title(title)
    if not c or not t:
        return None
    place = fold(location.city or location.country or ("remote" if location.remote else ""))
    return "fz:" + sha1(f"{c}|{t}|{place}")


def content_hash_key(raw: RawJob) -> str:
    return "hash:" + sha1(f"{normalize_company(raw.company)}|{normalize_title(raw.title)}|{fold(raw.location_raw)}")


WEAK_PREFIXES = ("url:", "fz:", "hash:")
NOT_A_NAMESPACE = {"http", "https", "urn", "tag", "mailto"}  # feed guids that are URLs name no source


def _source_ids(keys) -> dict[str, set[str]]:
    """Ids grouped by the source that issued them: {"francetravail": {"francetravail:204xyz"}, ...}"""
    out: dict[str, set[str]] = {}
    for key in keys:
        namespace = key.split(":", 1)[0]
        if not key.startswith(WEAK_PREFIXES) and ":" in key and namespace not in NOT_A_NAMESPACE:
            out.setdefault(namespace, set()).add(key)
    return out


def _conflict(a: dict, b: dict) -> bool:
    """Do two id sets name different postings? True when a source both know gave them different ids."""
    return any(namespace in b and not (ids & b[namespace]) for namespace, ids in a.items())


def _native_ids(keys) -> set[str]:
    return {k for k in keys if not k.startswith(("url:", "fz:", "hash:")) and k.split(":")[0] in {
        "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "personio", "workday"}}


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _pick(raws: list[RawJob], attr: str, predicate=lambda v: bool(v)):
    for raw in raws:
        value = getattr(raw, attr)
        if predicate(value):
            return value
    return None


def build_alias_index(jobs: dict) -> dict[str, str]:
    index = {}
    newest = sorted(jobs, key=lambda cid: jobs[cid].get("last_seen") or "", reverse=True)
    for cid in newest:
        index[cid.lower()] = cid  # a record's own id always wins over another record's alias
    for cid in newest:  # for a shared alias (a fuzzy key after a repost) the record seen last wins
        for alias in jobs[cid].get("aliases") or []:
            index.setdefault(alias, cid)
    return index


def fuse(raws: list[RawJob], state_jobs: dict, now) -> list[FusedJob]:
    if not raws:
        return []
    raws = [r for r in raws if r.title and r.title.strip()]
    n = len(raws)
    uf = _UnionFind(n)
    keys_per_raw = [strong_keys(r) or [content_hash_key(r)] for r in raws]
    locs = [parse_location(r.location_raw, r.remote_flag, r.workplace_type) for r in raws]
    fuzz = [fuzzy_key(r.company, r.title, locs[i]) for i, r in enumerate(raws)]

    owner: dict[str, int] = {}
    for i, keys in enumerate(keys_per_raw):  # ids first: the same id is the same posting, whatever else differs
        for key in keys:
            if key.startswith("url:"):
                continue
            if key in owner:
                uf.union(owner[key], i)
            else:
                owner[key] = i

    def group_keys(root):
        return [k for j in range(n) if uf.find(j) == root for k in keys_per_raw[j]]

    def group_natives(root):
        return _native_ids(group_keys(root))

    for i, keys in enumerate(keys_per_raw):  # then URLs, which two postings can share (a generic application page)
        for key in keys:
            if not key.startswith("url:"):
                continue
            if key not in owner:
                owner[key] = i
                continue
            a, b = uf.find(owner[key]), uf.find(i)
            if a != b and not _conflict(_source_ids(group_keys(a)), _source_ids(group_keys(b))):
                uf.union(a, b)

    fuzzy_owner: dict[str, int] = {}
    for i, fk in enumerate(fuzz):
        if not fk:
            continue
        if fk not in fuzzy_owner:
            fuzzy_owner[fk] = i
            continue
        a, b = uf.find(fuzzy_owner[fk]), uf.find(i)
        if a == b:
            continue
        na, nb = group_natives(a), group_natives(b)
        if na and nb and not (na & nb):
            continue  # two distinct ATS postings with identical titles: keep separate
        uf.union(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    alias_index = build_alias_index(state_jobs)
    by_cid: dict[str, list[int]] = {}
    for members in groups.values():
        all_keys = list(dict.fromkeys(k for i in members for k in keys_per_raw[i]))
        fuzzy_keys = [fuzz[i] for i in members if fuzz[i]]
        cid = None
        my_ids = _source_ids(all_keys)
        for key in all_keys:
            candidate = alias_index.get(key)
            if candidate is None:
                continue
            if key.startswith("url:") and _conflict(my_ids, _source_ids(
                    [candidate.lower()] + list(state_jobs.get(candidate, {}).get("aliases") or []))):
                continue  # same application page, another offer: never attach it to the old, already alerted record
            cid = candidate
            break
        if cid is None and fuzzy_keys:
            mine = _native_ids(all_keys)
            for fk in fuzzy_keys:
                candidate = alias_index.get(fk)
                if not candidate:
                    continue
                known = state_jobs.get(candidate, {})
                rec_natives = _native_ids(known.get("aliases") or [])
                if rec_natives and mine and not (rec_natives & mine):
                    continue
                last_seen = parse_datetime(known.get("last_seen"))
                if last_seen is not None and now - last_seen > timedelta(days=FUZZY_STATE_MATCH_DAYS):
                    continue  # gone for two months and back: a repost, which deserves its own alert
                cid = candidate
                break
        if cid is None:
            natives = sorted(_native_ids(all_keys))
            cid = natives[0] if natives else all_keys[0]
        by_cid.setdefault(cid, []).extend(members)

    fused: list[FusedJob] = []
    for cid, members in by_cid.items():
        members.sort(key=lambda i: -raws[i].priority)
        group_raws = [raws[i] for i in members]
        all_keys = list(dict.fromkeys(k for i in members for k in keys_per_raw[i]))
        fuzzy_keys = [fuzz[i] for i in members if fuzz[i]]
        best_loc = locs[members[0]]

        description = ""
        for raw in group_raws:  # best-priority *full* description, else the longest excerpt
            if len(raw.description or "") >= FULL_DESCRIPTION_CHARS:
                description = raw.description
                break
        if not description:
            description = max((r.description or "" for r in group_raws), key=len)

        apply_url = None
        for raw in group_raws:
            for candidate in (raw.apply_url, raw.url):
                if candidate and not is_aggregator(candidate):
                    apply_url = candidate
                    break
            if apply_url:
                break
        apply_url = apply_url or _pick(group_raws, "apply_url") or _pick(group_raws, "url")

        location = best_loc
        for i in members:  # prefer the most informative location among top sources
            if locs[i].raw and (not location.raw or (location.remote is None and locs[i].remote is not None)):
                location = locs[i]
                break
        if location.remote is None and FULLY_REMOTE_TEXT_RE.search(description or ""):
            location.remote, location.work_mode = True, "remote"

        reliable = [r for r in group_raws if r.posted_at and r.posted_at_reliable]
        posted_source = reliable[0] if reliable else next((r for r in group_raws if r.posted_at), None)
        top = group_raws[0]
        aliases = list(dict.fromkeys(all_keys + fuzzy_keys))[:25]
        fused.append(FusedJob(
            canonical_id=cid, raws=group_raws, title=top.title.strip(), company=_pick(group_raws, "company"),
            url=_pick(group_raws, "url"), apply_url=apply_url, description=description, location=location,
            employment_type=_pick(group_raws, "employment_type"), salary=_pick(group_raws, "salary"),
            posted_at=posted_source.posted_at if posted_source else None,
            posted_at_reliable=bool(reliable), priority=top.priority, extraction_method=top.extraction_method,
            from_rotation=all(r.from_rotation for r in group_raws),
            board_keys={r.board_key for r in group_raws if r.board_key}, aliases=aliases,
            fuzzy_key=fuzzy_keys[0] if fuzzy_keys else None,
        ))
    return fused


__all__ = ["FusedJob", "fuse", "strong_keys", "fuzzy_key", "build_alias_index", "SOURCE_PRIORITY"]
