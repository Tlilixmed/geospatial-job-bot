"""Command-line entry point.

  python -m geojobbot run                 full run (DRY_RUN=true to print alerts instead of sending)
  python -m geojobbot validate-sources    check every configured ATS board / career site / feed
  python -m geojobbot diagnose URL        fetch one public URL, extract and explain the score
  python -m geojobbot score --title T [--description-file F] [--location L]   offline scoring
  python -m geojobbot inspect [--job Q] [--sources] [--boards STATUS] [--run latest [--grep Q]]
  python -m geojobbot selftest-r2         live R2 round-trip under selftest/ (never touches state)
  python -m geojobbot selftest-telegram   send one test message
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import uuid

from .config import load_settings
from .core.pipeline import API_HOST_DELAYS, SecretRedactingFilter, ConfigError, Pipeline, build_store
from .matching.matcher import score_job
from .scrapers.ats.base import ATSAdapter
from .scrapers.ats.detect import detect, parse_workday_url, BoardRef
from .scrapers.ats.more_ats import all_adapters
from .scrapers.base import RunContext
from .scrapers.generic import extract_jobs
from .storage.state import StateManager
from .utils.dates import utcnow
from .utils.http import FetchError, HttpClient
from .utils.location import parse_location
from .utils.robots import RobotsCache


def _setup_logging(settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(SecretRedactingFilter(settings.secrets()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(getattr(logging, (settings.log_level or "INFO").upper(), logging.INFO))
    for noisy in ("botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _light_ctx(settings, state=None):
    client = HttpClient(settings.user_agent, default_delay=settings.default_host_delay, host_delays=API_HOST_DELAYS,
                        timeout=settings.http_timeout)
    client.robots = RobotsCache(client, settings.robots_agent_token)
    from .core.boards import BoardRegistry
    state = state if state is not None else {"boards": {}, "cursors": {}, "page_cache": {}}
    now = utcnow()
    return RunContext(settings, client, state, run_id="cli", now=now, registry=BoardRegistry(state, now),
                      match_config=settings.match_config())


def _print_score(title, description, location_raw, settings, remote_flag=None, workplace=None):
    loc = parse_location(location_raw, remote_flag, workplace)
    result = score_job(title, description, loc.to_dict(), settings.match_config())
    print(f"  Title:     {title}")
    print(f"  Location:  {loc.display()}  (remote={loc.remote}, scope={loc.remote_scope})")
    print(f"  Score:     {result.score}/100  tier={result.tier}  title_kind={result.title_kind}")
    print(f"  Breakdown: {result.breakdown}")
    print(f"  Skills:    {[h.canonical + (' (' + h.qualifier + ')' if h.qualifier else '') for h in result.skills]}")
    print(f"  Domains:   {result.domain_names}")
    print(f"  Geo signal families: {result.geo_families}")
    print(f"  Why:       {result.why_matched}")
    print(f"  Rejection: {result.rejection_reasons or '-'}")
    print(f"  Description length: {len(description or '')}")
    return result


def cmd_run(settings, args) -> int:
    code, _ = Pipeline(settings).run()
    return code


def cmd_validate(settings, args) -> int:
    ctx = _light_ctx(settings)
    adapters = all_adapters()
    ats_cfg = (settings.sources or {}).get("ats", {}) or {}
    problems = 0
    print(f"{'ATS':<16}{'BOARD':<40}{'STATUS':<18}{'LISTED':>7}  NOTE")
    for ats, slugs in ats_cfg.items():
        adapter = adapters.get(ats)
        if adapter is None:
            print(f"{ats:<16}{'(unknown ATS in sources.toml)':<40}")
            problems += 1
            continue
        for slug in slugs or []:
            ref = parse_workday_url(slug) if ats == "workday" else BoardRef(ats, slug)
            if ref is None:
                print(f"{ats:<16}{slug[:39]:<40}{'INVALID':<18}{'':>7}  not a Workday career-site URL")
                problems += 1
                continue
            try:
                res = adapter.fetch_board(ctx, ref, geo_context=True, variant=None, company_hint=None)
            except FetchError as exc:
                res = ATSAdapter.error_result(exc)
            except Exception as exc:
                from .scrapers.ats.base import BoardResult
                res = BoardResult("SCHEMA_MISMATCH", error=f"{type(exc).__name__}: {exc}")
            if res.status != "VALID":
                problems += 1
            print(f"{ats:<16}{ref.slug[:39]:<40}{res.status:<18}{res.listed:>7}  {res.error or ''}")
    for site in (settings.sources or {}).get("career_sites", []) or []:
        try:
            ctx.client.get(site["url"])
            status, note = "REACHABLE", ""
        except FetchError as exc:
            status, note = exc.kind, str(exc)
            problems += 1
        print(f"{'career_site':<16}{site.get('name', site['url'])[:39]:<40}{status:<18}{'':>7}  {note}")
    print(f"\n{problems} source(s) need attention" if problems else "\nAll configured sources look valid")
    return 3 if problems else 0


def cmd_diagnose(settings, args) -> int:
    ctx = _light_ctx(settings)
    url = args.url
    native, board = detect(url)
    jobs = None
    if board is not None:
        adapter = all_adapters().get(board.ats)
        print(f"ATS detected: {board.ats} board={board.slug} native_id={native}")
        try:
            if native and adapter is not None:
                jobs = adapter.fetch_job(ctx, url)
            if jobs is None and adapter is not None:
                res = adapter.fetch_board(ctx, board, geo_context=True, variant=None, company_hint=None)
                print(f"Board status: {res.status} listed={res.listed} prefiltered_out={res.prefiltered_out} {res.error or ''}")
                jobs = res.jobs
        except FetchError as exc:
            print(f"Fetch failed: {exc}")
            return 1
    if jobs is None:
        try:
            response = ctx.client.get(url)
        except FetchError as exc:
            print(f"Fetch failed: {exc}")
            return 1
        extraction = extract_jobs(response.text, response.url or url, ctx.now)
        print(f"Generic extraction method: {extraction.method} expired={extraction.expired} errors={extraction.errors}")
        jobs = extraction.jobs
    if not jobs:
        print("No job postings extracted.")
        return 1
    for job in jobs:
        print("-" * 60)
        print(f"  Source: {job.source_name} ({job.extraction_method})  company={job.company}  posted={job.posted_at}")
        print(f"  Prefilter (geo_context=False): {ctx.prefilter(job.title, False)}")
        _print_score(job.title, job.description, job.location_raw, settings, job.remote_flag, job.workplace_type)
    return 0


def cmd_score(settings, args) -> int:
    description = ""
    if args.description_file:
        with open(args.description_file, encoding="utf-8") as fh:
            description = fh.read()
    _print_score(args.title, description, args.location or "", settings)
    return 0


def cmd_inspect(settings, args) -> int:
    try:
        store = build_store(settings)
    except ConfigError as exc:
        print(exc)
        return 1
    manager = StateManager(store, settings.state_prefix)
    if args.run:
        report = manager.read_json("runs/latest.json")
        if not report:
            print("no runs/latest.json")
            return 1
        if args.run != "latest" or args.grep:
            day = report["run_id"][:4] + "-" + report["run_id"][4:6] + "-" + report["run_id"][6:8]
            full = manager.read_json(f"runs/{day}/{report['run_id']}.json.gz") or {}
            rows = [r for r in full.get("diagnostics", [])
                    if not args.grep or args.grep.lower() in json.dumps(r).lower()]
            print(json.dumps(rows[:50], indent=2, ensure_ascii=False))
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    state = manager.load()
    if args.sources:
        print(json.dumps(state.get("sources", {}), indent=2))
    if args.boards:
        rows = {k: v for k, v in state.get("boards", {}).items() if args.boards == "all" or v.get("status") == args.boards}
        print(json.dumps(rows, indent=2))
        print(f"{len(rows)} boards")
    if args.job:
        q = args.job.lower()
        hits = [r for r in state["jobs"].values() if q in json.dumps(r).lower()]
        print(json.dumps(hits[:20], indent=2, ensure_ascii=False))
        print(f"{len(hits)} matching jobs")
    if not (args.sources or args.boards or args.job):
        tiers = {}
        for rec in state["jobs"].values():
            tiers[rec.get("tier")] = tiers.get(rec.get("tier"), 0) + 1
        print(json.dumps({"jobs": len(state["jobs"]), "tiers": tiers, "boards": len(state["boards"]),
                          "last_run_id": state.get("last_run_id"), "updated_at": state.get("updated_at")}, indent=2))
    return 0


def cmd_selftest_r2(settings, args) -> int:
    if not settings.r2_configured:
        print("R2 is not configured (R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME)")
        return 1
    store = build_store(settings)
    key = f"{settings.state_prefix.strip('/') + '/' if settings.state_prefix.strip('/') else ''}selftest/{uuid.uuid4().hex}.json.gz"
    payload = gzip.compress(json.dumps({"hello": "r2", "at": utcnow().isoformat()}).encode())
    steps = []
    try:
        store.put_bytes(key, payload, "application/gzip")
        steps.append(("put", True))
        steps.append(("head", (store.head(key) or {}).get("size") == len(payload)))
        steps.append(("get round-trip", store.get_bytes(key) == payload))
        steps.append(("copy", store.copy(key, key + ".copy") is None))
        steps.append(("list", key in store.list_keys(key.rsplit("/", 1)[0] + "/")))
        steps.append(("delete", store.delete_keys([key, key + ".copy"]) == 2))
        steps.append(("missing -> None", store.get_bytes(key) is None))
    except Exception as exc:
        print(f"R2 self-test error: {type(exc).__name__}: {exc}")
        return 1
    for name, ok in steps:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in steps) else 1


def cmd_selftest_telegram(settings, args) -> int:
    if not settings.telegram_configured:
        print("Telegram is not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return 1
    from .notifications.telegram import TelegramNotifier
    ok, error = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id).send(
        "✅ <b>Geospatial job bot</b> — Telegram integration test succeeded.")
    print("PASS: message delivered" if ok else f"FAIL: {error}")
    return 0 if ok else 1


def cmd_commands(settings, args) -> int:
    """Poll Telegram once, execute pending commands and reply (used by .github/workflows/commands.yml)."""
    if not settings.telegram_configured:
        print("Telegram is not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
        return 1
    from .core.prefs import apply_prefs, load_prefs
    from .notifications.commands import CommandProcessor
    from .notifications.telegram import TelegramNotifier
    try:
        manager = StateManager(build_store(settings), settings.state_prefix)
        apply_prefs(settings, load_prefs(manager))
        notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id,
                                    delay_s=settings.telegram_delay_s)
        counts = CommandProcessor(settings, manager, notifier).run()
    except ConfigError as exc:
        print(exc)
        return 1
    except Exception as exc:  # never print the exception text: Telegram URLs contain the token
        print(f"command processing failed: {type(exc).__name__}")
        return 1
    print(f"telegram commands: {dict(counts) or 'nothing pending'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="geojobbot", description="Geospatial job discovery bot")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run")
    sub.add_parser("validate-sources")
    p = sub.add_parser("diagnose")
    p.add_argument("url")
    p = sub.add_parser("score")
    p.add_argument("--title", required=True)
    p.add_argument("--description-file")
    p.add_argument("--location")
    p = sub.add_parser("inspect")
    p.add_argument("--job")
    p.add_argument("--sources", action="store_true")
    p.add_argument("--boards", help="INVALID | VALID | EMPTY_UNVERIFIED | ERROR | all")
    p.add_argument("--run", help="'latest', or 'diag' to print match diagnostics")
    p.add_argument("--grep")
    sub.add_parser("selftest-r2")
    sub.add_parser("selftest-telegram")
    sub.add_parser("commands")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 1
    _setup_logging(settings)
    handlers = {"run": cmd_run, None: cmd_run, "validate-sources": cmd_validate, "diagnose": cmd_diagnose,
                "score": cmd_score, "inspect": cmd_inspect, "selftest-r2": cmd_selftest_r2,
                "selftest-telegram": cmd_selftest_telegram, "commands": cmd_commands}
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
