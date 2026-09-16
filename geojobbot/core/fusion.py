"""Canonical identity, cross-source grouping and field fusion.

Identity keys per observation (strongest first):
  native ATS id      greenhouse:123 / lever:<uuid> / ...   (also recovered from URLs such as ?gh_jid=)
  source job id      remotive:987
  canonical URL      url:<sha1>
  content hash       hash:<sha1 of company|title|location>
  fuzzy key          fz:<sha1 of normalised company|title|place>  (weak; guarded)

Observations sharing any strong key are merged. Fuzzy keys merge groups only when that cannot
join two different native ATS postings. Existing state records are matched through their stored
aliases so a job keeps the same canonical id across runs and sources.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import SOURCE_PRIORITY, RawJob
from ..utils.dates import to_iso
from ..utils.location import FULLY_REMOTE_TEXT_RE, ParsedLocation, parse_location
from ..utils.text import fold, normalize_company, normalize_title, sha1
from ..utils.urls import canonicalize_url, is_aggregator

FUZZY_STATE_MATCH_DAYS = 60


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
    for cid, rec in jobs.items():
        index.setdefault(cid.lower(), cid)
        for alias in rec.get("aliases") or []:
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
    for i, keys in enumerate(keys_per_raw):
        for key in keys:
            if key in owner:
                uf.union(owner[key], i)
            else:
                owner[key] = i

    def group_natives(root):
        return set().union(*[_native_ids(keys_per_raw[j]) for j in range(n) if uf.find(j) == root])

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
        cid = next((alias_index[k] for k in all_keys if k in alias_index), None)
        if cid is None and fuzzy_keys:
            mine = _native_ids(all_keys)
            for fk in fuzzy_keys:
                candidate = alias_index.get(fk)
                if not candidate:
                    continue
                rec_natives = _native_ids(state_jobs.get(candidate, {}).get("aliases") or [])
                if rec_natives and mine and not (rec_natives & mine):
                    continue
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
        for raw in group_raws:  # best-priority description of meaningful length, else the longest
            if len(raw.description or "") >= 200:
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
