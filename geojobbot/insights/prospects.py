"""Go where sponsorship is proven: find the job boards of employers we have a reason to watch.

Targets, in this order:
  1. the user's watch list (/watch company)
  2. firms that just won a geospatial contract (procurement signals)
  3. Canadian employers whose positive LMIAs were for surveying / geomatics occupations
  4. licensed UK and Dutch sponsors whose name says surveying, geomatics, mapping, LiDAR…
For a few targets per run the backend derives the slugs a company would plausibly use and asks each public ATS API
whether such a board exists. A board is kept only when the ATS reports a company name that matches (or the slug
contains the whole name); it then joins the board registry with origin "prospect": read once a day, and every run
while it produces relevant jobs. Nothing is guessed silently: /prospects lists
what was found and why each employer was tried.
"""
from __future__ import annotations

import logging
import re
from datetime import timedelta

from ..models import BackendOutput
from ..scrapers.ats.detect import BoardRef
from ..scrapers.base import Backend, RunContext
from ..utils.dates import parse_datetime, to_iso
from ..utils.http import FetchError
from ..utils.text import normalize_company
from .sponsors import core_name

log = logging.getLogger(__name__)

GEO_NAME_RE = re.compile(r"\b(survey\w*|geomati\w+|geospatial|geo ?spatial|mapping|cartograph\w*|lidar|photogramm\w*|"
                         r"hydrograph\w*|geodes\w*|geodata|geoinformati\w*|topograph\w*|landmeet\w*|remote sensing|"
                         r"gis|bathymetr\w*|land information)\b")
NOT_GEO_NAME_RE = re.compile(r"\b(quantity|chartered|building survey\w*|market research|opinion|polling|insurance|valuation|valuers?|"
                             r"estate|property|marine surveyors? and|pest|damp|asbestos)\b")
# Personio is left out: an unknown account is an unresolvable host there, which costs 15 s of retries per guess
PROBE_ATS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee")
GENERIC_TOKENS = {"land", "survey", "surveys", "surveying", "surveyors", "geomatics", "mapping", "group", "services", "service",
                  "solutions", "consulting", "consultants", "engineering", "associates", "partners", "company", "global",
                  "international", "north", "south", "east", "west", "first", "general", "national", "new", "city", "county"}
RECHECK_DAYS = 120
MAX_PROSPECTS = 4000
PRIORITY = {"watch": 0, "award": 1, "lmia": 2, "register": 3}


def slug_candidates(name: str) -> list[str]:
    """Slugs a company would plausibly use, most specific first (at most three)."""
    tokens = core_name(normalize_company(name)).split()
    if not tokens:
        return []
    joined = "".join(tokens)
    out = [joined]
    if len(tokens) > 1:
        out.append("-".join(tokens))
        if len(tokens[0]) >= 5 and tokens[0] not in GENERIC_TOKENS:
            out.append(tokens[0])
    return [s for s in dict.fromkeys(out) if 3 <= len(s) <= 40][:3]


def company_matches(target: str, reported: str | None, slug: str) -> bool:
    """Is the board the ATS returned this employer's? Guards against 'summit' meaning some other Summit."""
    want = core_name(normalize_company(target)).split()
    if not want:
        return False
    if reported is None:  # the ATS did not name the company: trust only a slug that spells the whole name
        return slug.replace("-", "") == "".join(want)
    got = core_name(normalize_company(reported)).split()
    if not got:
        return False
    if got == want or "".join(got) == "".join(want):
        return True
    common = set(want) & set(got)
    if want[0] not in common:
        return False
    if len(want) == 1 or len(got) == 1:
        return len(want) == 1 and len(got) <= 3  # "Fugro" ~ "Fugro Canada Corp"; never "Summit" ~ "Summit Geomatics"
    return len(common) >= 2


def collect_targets(state: dict, sponsor_data: dict | None, watch: list[dict] | None) -> list[dict]:
    """[{key, name, kind, why, country}] for every employer worth prospecting, most valuable first."""
    targets: dict[str, dict] = {}

    def add(name, kind, why, country=None):
        key = normalize_company(name)
        if len(key) < 3:
            return
        known = targets.get(key)
        if known is None or PRIORITY[kind] < PRIORITY[known["kind"]]:
            targets[key] = {"key": key, "name": name.strip(), "kind": kind, "why": why, "country": country}

    for item in watch or []:
        if item.get("name"):
            add(item["name"], "watch", "on your watch list")
    for signal in (state.get("signals") or {}).get("items") or []:
        if signal.get("kind") == "award" and signal.get("winner"):
            add(signal["winner"], "award", f"won: {(signal.get('title') or 'a geospatial contract')[:80]}", signal.get("winner_country"))
    registers = (sponsor_data or {}).get("registers") or {}
    for entry in (registers.get("ca") or {}).values():
        if entry.get("g") and entry.get("n"):
            add(entry["n"], "lmia", "LMIA approved for " + ", ".join((entry.get("o") or ["geomatics occupations"])[:2]), "Canada")
    for code, country in (("uk", "United Kingdom"), ("nl", "Netherlands")):
        for norm, entry in (registers.get(code) or {}).items():
            if GEO_NAME_RE.search(norm) and not NOT_GEO_NAME_RE.search(norm) and entry.get("n"):
                add(entry["n"], "register", f"licensed sponsor in {country}", country)
    return sorted(targets.values(), key=lambda t: (PRIORITY[t["kind"]], t["key"]))


class ProspectBackend(Backend):
    """Discovery-phase backend: boards it finds are read by the ATS backends later in the same run."""

    name = "prospects"
    phase = "discovery"
    source_type = "ats"

    def __init__(self, adapters: dict, sponsor_data=None, watch=None):
        self.adapters = adapters
        self.sponsor_data = sponsor_data  # callable or dict: loaded lazily so a missing register never blocks a run
        self.watch = watch

    def enabled(self, ctx: RunContext):
        ok, reason = super().enabled(ctx)
        if ok and not ctx.settings.prospects_per_run:
            return False, "PROSPECTS_PER_RUN=0"
        return ok, reason

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        data = self.sponsor_data() if callable(self.sponsor_data) else self.sponsor_data
        watch = self.watch() if callable(self.watch) else self.watch
        targets = collect_targets(ctx.state, data, watch)
        with ctx.lock:
            book = ctx.state.setdefault("prospects", {})
        recheck = ctx.now - timedelta(days=RECHECK_DAYS)
        due = [t for t in targets
               if (parse_datetime((book.get(t["key"]) or {}).get("checked")) or recheck) <= recheck
               or (t["kind"] == "watch" and not (book.get(t["key"]) or {}).get("checked"))]
        found_boards, probed, errors = 0, 0, 0
        for target in due[: ctx.settings.prospects_per_run]:
            if ctx.out_of_time(reserve_s=600):
                break
            boards = self._probe(ctx, target, out)
            probed += 1
            errors += boards is None
            with ctx.lock:
                previous = book.get(target["key"]) or {}
                book[target["key"]] = {"name": target["name"], "kind": target["kind"], "why": target["why"],
                                       "country": target["country"], "checked": to_iso(ctx.now),
                                       "boards": sorted(set(previous.get("boards") or []) | set(boards or []))}
            found_boards += len(boards or [])
        with ctx.lock:  # drop entries that are no longer targets, and cap the book
            keep = {t["key"] for t in targets}
            for key in [k for k in book if k not in keep and not book[k].get("boards")]:
                del book[key]
            for key in sorted(book, key=lambda k: book[k].get("checked") or "")[: max(0, len(book) - MAX_PROSPECTS)]:
                del book[key]
        out.details = {"targets": len(targets), "due": len(due), "probed": probed, "boards_found": found_boards,
                       "probe_errors": errors}
        if not targets:
            out.status, out.error = "SKIPPED", "no targets yet (sponsor registers not loaded, no signals, empty watch list)"
        return out

    def _probe(self, ctx: RunContext, target: dict, out: BackendOutput) -> list[str] | None:
        """Board keys found for one employer; None when every probe failed for technical reasons."""
        found, failures, attempts = [], 0, 0
        for slug in slug_candidates(target["name"]):
            for ats in PROBE_ATS:
                adapter = self.adapters.get(ats)
                if adapter is None or ats in ctx.settings.disabled_backends:
                    continue
                ref = BoardRef(ats, slug)
                known = ctx.registry.entry(ref.key)
                if known is not None:  # already in the registry: a hit only if it is alive and ours
                    if known.get("status") == "VALID" and company_matches(target["name"], known.get("company"), slug):
                        found.append(ref.key)
                    continue
                attempts += 1
                try:
                    result = adapter.fetch_board(ctx, ref, geo_context=True, variant=None, company_hint=target["name"])
                except FetchError as exc:
                    failures += exc.kind != "NOT_FOUND"
                    continue
                except Exception as exc:  # a malformed payload from a guessed board is not worth a traceback
                    log.debug("prospect probe %s failed: %s", ref.key, type(exc).__name__)
                    failures += 1
                    continue
                if result.status != "VALID" or not result.listed:
                    continue  # unknown account, or an ATS that answers "empty" for anything
                if not company_matches(target["name"], result.company, slug):
                    continue
                ctx.registry.register(ref, "prospect")  # read daily from now on (BoardRegistry.select)
                ctx.registry.record(ref, "VALID", job_count=result.listed, variant=result.variant,
                                    company=result.company or target["name"])
                for job in result.jobs:  # the probe already read the board: keep what it found
                    job.board_key, job.geo_context, job.from_rotation = ref.key, True, False
                out.jobs.extend(result.jobs)
                out.prefiltered_out += result.prefiltered_out
                found.append(ref.key)
                log.info("prospect %s -> %s (%d jobs)", target["name"], ref.key, result.listed)
            if found:
                break  # the most specific slug matched: no need to try looser ones
        return None if attempts and failures == attempts else found


def format_prospects(state: dict, limit: int = 15) -> str:
    book = state.get("prospects") or {}
    boards = state.get("boards") or {}
    hits = [p for p in book.values() if p.get("boards")]
    if not book:
        return ("🧭 No employers prospected yet. After the sponsor registers load, each run looks up a few employers "
                "proven to sponsor geomatics staff and adds their job boards. /watch company adds one yourself.")
    hits.sort(key=lambda p: (PRIORITY.get(p.get("kind"), 9), -sum(int((boards.get(b) or {}).get("relevant_total") or 0) for b in p["boards"])))
    by_kind = {k: sum(1 for p in book.values() if p.get("kind") == k) for k in PRIORITY}
    lines = ["🧭 <b>Employers I went looking for</b>",
             f"<i>{len(book)} checked so far · {len(hits)} have a job board I can read · "
             + " · ".join(f"{n} {k}" for k, n in by_kind.items() if n) + "</i>"]
    for p in hits[:limit]:
        relevant = sum(int((boards.get(b) or {}).get("relevant_total") or 0) for b in p["boards"])
        listed = sum(int((boards.get(b) or {}).get("last_job_count") or 0) for b in p["boards"])
        lines += ["", f"• <b>{_esc(p['name'])}</b>" + (f" — {_esc(p['country'])}" if p.get("country") else ""),
                  f"   {_esc(p.get('why') or '')}",
                  f"   {_esc(', '.join(p['boards']))} · {listed} open jobs · {relevant} relevant so far"]
    if not hits:
        lines += ["", "None of them uses a job board I can read yet. Most small survey firms post on their own site: "
                      "/watch https://firm.example/careers adds such a page."]
    lines += ["", "/watch company or careers-page URL · /unwatch name · relevant jobs from these boards arrive as normal alerts"]
    return "\n".join(lines)[:4090]


def _esc(text) -> str:
    from html import escape

    return escape(str(text or ""), quote=False)
