"""Run summary (console + GitHub step summary) and match diagnostics."""
from __future__ import annotations

from ..utils.text import truncate


def diagnostics_rows(evaluated) -> list[dict]:
    rows = []
    for fused, result, rec in evaluated:
        rows.append({
            "canonical_id": rec.canonical_id, "title": rec.title, "company": rec.company,
            "location": rec.location_raw, "score": result.score, "tier": result.tier,
            "breakdown": result.breakdown, "title_kind": result.title_kind,
            "skills": rec.matched_skills, "domains": rec.matched_domains,
            "responsibilities": [h.canonical for h in result.responsibilities],
            "geo_families": result.geo_families, "why_matched": result.why_matched,
            "rejection_reasons": result.rejection_reasons, "description_length": len(fused.description or ""),
            "sources": sorted({r.source_name for r in fused.raws}), "url": rec.apply_url or rec.url,
            "notified": rec.notified,
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def _status_line(result: dict) -> str:
    detail = result.get("details") or {}
    extra = []
    if result.get("jobs"):
        extra.append(f"{result['jobs']} jobs")
    if result.get("prefiltered_out"):
        extra.append(f"{result['prefiltered_out']} prefiltered")
    if detail.get("boards_checked") is not None:
        extra.append(f"{detail['boards_checked']} boards")
    if result.get("error"):
        extra.append(truncate(result["error"], 90))
    suffix = f" ({'; '.join(extra)})" if extra else ""
    return f"  {result['name']}: {result['status']}{suffix}"


def build_summary(report: dict) -> str:
    lines = ["=" * 60, "RUN SUMMARY", "=" * 60, f"Run {report.get('run_id')} · code {report.get('code') or 'local'}",
             "", "Sources:"]
    for result in report["sources"]:
        lines.append(_status_line(result))
    invalid = report.get("invalid_configured", {})
    if any(invalid.values()):
        lines += ["", "INVALID configured sources (fix sources.toml):"]
        for ats, slugs in invalid.items():
            if slugs:
                lines.append(f"  {ats}: {', '.join(slugs)}")
    unverified = report.get("unverified_configured", {})
    if any(unverified.values()):
        lines += ["", "Unverified configured sources (empty or unexpected response):"]
        for ats, slugs in unverified.items():
            if slugs:
                lines.append(f"  {ats}: {', '.join(slugs)}")
    c = report["counts"]
    lines += [
        "",
        f"Raw jobs collected: {c.get('raw', 0)}",
        f"Prefiltered (irrelevant titles, not stored): {c.get('prefiltered_out', 0)}",
        f"Unique jobs after fusion: {c.get('unique', 0)}",
        f"New jobs: {c.get('new', 0)}",
        f"Already seen: {c.get('already_seen', 0)}",
        f"Updated jobs: {c.get('updated', 0)}",
        "",
        "Matches:",
        f"  High: {c.get('tier_high', 0)}",
        f"  Possible: {c.get('tier_possible', 0)}",
        f"  Rejected: {c.get('tier_rejected', 0)}",
        "",
        "Alerts:",
        f"  Sent: {c.get('alerts_sent', 0)} job(s) in {c.get('messages_sent', 0)} message(s)",
        f"  Failed: {c.get('alerts_failed', 0)}",
        f"  Blocked (stale posting): {c.get('blocked_stale', 0)}",
        f"  Deferred (per-run cap): {c.get('deferred_by_cap', 0)}",
        f"  Muted / hidden or applied / held while paused: {c.get('muted', 0)} / {c.get('hidden', 0)} / {c.get('paused', 0)}",
        f"  Pending (Telegram not configured / dry run): {c.get('alerts_pending', 0)}",
        "",
        "Discovery:",
        f"  New boards registered: {report.get('new_boards', {})}",
        f"  Boards known: {c.get('boards_known', 0)}",
        "",
        "R2 / state:",
        f"  Backend: {report['storage']['backend']}",
        f"  State loaded: {'YES' if report['storage']['loaded'] else 'NO'}"
        + (" (first run)" if report['storage'].get('first_run') else "")
        + (f" (restored from {report['storage']['restored_from_backup']})" if report['storage'].get('restored_from_backup') else ""),
        f"  State saved: {'YES' if report['storage']['saved'] else 'NO'}",
        f"  Raw snapshots stored: {report['storage'].get('snapshots', 0)}",
        f"  Jobs in state: {c.get('jobs_in_state', 0)}",
        "",
        f"HTTP: {report.get('http', {}).get('requests', 0)} requests, "
        f"{report.get('http', {}).get('retries', 0)} retries, errors {report.get('http', {}).get('errors', {})}",
        f"Parser errors: {report.get('parser_errors', {})}",
        f"Run duration: {report.get('duration_s', 0):.0f}s",
    ]
    if report.get("fatal"):
        lines += ["", f"FATAL: {report['fatal']}"]
    return "\n".join(lines)


def build_markdown(report: dict) -> str:
    c = report["counts"]
    md = [f"## Geospatial job bot — run `{report['run_id']}` · code `{report.get('code') or 'local'}`", "",
          f"**High:** {c.get('tier_high', 0)} · **Possible:** {c.get('tier_possible', 0)} · "
          f"**Rejected:** {c.get('tier_rejected', 0)} · **Alerts sent:** {c.get('alerts_sent', 0)} · "
          f"**New jobs:** {c.get('new', 0)}", "", "| Source | Status | Jobs | Note |", "|---|---|---|---|"]
    for result in report["sources"]:
        note = truncate(result.get("error") or "", 80).replace("|", "/")
        md.append(f"| {result['name']} | {result['status']} | {result.get('jobs', 0)} | {note} |")
    invalid = {k: v for k, v in report.get("invalid_configured", {}).items() if v}
    if invalid:
        md += ["", "### ⚠️ Invalid configured sources"] + [f"- **{k}**: {', '.join(v)}" for k, v in invalid.items()]
    if report.get("fatal"):
        md += ["", f"### ❌ Fatal: {report['fatal']}"]
    md += ["", "```", build_summary(report), "```"]
    return "\n".join(md)
