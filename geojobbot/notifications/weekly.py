"""Weekly summary: what was alerted, what you applied to and whether it is still listed, what is still open."""
from __future__ import annotations

from datetime import timedelta

from ..core.jobs import alert_block_reason
from ..models import TIER_HIGH
from ..utils.dates import parse_datetime
from ..utils.text import fold, job_code, normalize_company, normalize_title
from .telegram import _esc

STILL_LISTED_DAYS = 4   # a posting not seen by any source for this long is treated as gone
OPEN_SHOWN = 7


def format_weekly(state: dict, prefs: dict, settings, now) -> str | None:
    """One Telegram message, or None when there is nothing worth saying."""
    jobs = state.get("jobs", {})
    week_ago = now - timedelta(days=7)
    alerted = [r for r in jobs.values() if (parse_datetime(r.get("notified_at")) or week_ago) > week_ago and r.get("notified")]
    applied = prefs.get("applied") or {}
    excluded = set(prefs.get("hidden") or []) | set(applied)
    muted = [fold(t) for t in prefs.get("muted") or [] if t.strip()]
    still_open = [r for cid, r in jobs.items()
                  if r.get("tier") == TIER_HIGH and cid not in excluded
                  and not any(t in fold(f"{r.get('title') or ''} {r.get('company') or ''}") for t in muted)
                  and alert_block_reason(r, settings, now) is None
                  and (parse_datetime(r.get("last_seen")) or now) > now - timedelta(days=STILL_LISTED_DAYS)]
    still_open.sort(key=lambda r: -int(r.get("score") or 0))
    if not (alerted or applied or still_open):
        return None

    lines = ["📊 <b>Weekly job summary</b>",
             f"<i>{now.strftime('%d %b %Y')} · {len(alerted)} alerted this week "
             f"({sum(1 for r in alerted if r.get('tier') == TIER_HIGH)} high) · {len(applied)} applications tracked</i>"]
    if applied:
        lines += ["", "<b>Your applications</b>"]
        rows = sorted(applied.items(), key=lambda kv: kv[1].get("at") or "", reverse=True)[:15]
        for cid, info in rows:
            rec = jobs.get(cid)
            seen = parse_datetime(rec.get("last_seen")) if rec else None
            listed = bool(seen and seen > now - timedelta(days=STILL_LISTED_DAYS))
            when = parse_datetime(info.get("at"))
            age = f"{(now - when).days}d ago" if when else "?"
            lines.append(f"{'🟢' if listed else '⚪'} {_esc(info.get('title'))}"
                         + (f" — {_esc(info['company'])}" if info.get("company") else "")
                         + f" · applied {age} · {'still listed' if listed else 'no longer listed'}")
    if still_open:
        # the same title at the same company posted for several locations is one line, not several
        groups: dict[tuple, list[dict]] = {}
        for rec in still_open:
            groups.setdefault((normalize_company(rec.get("company")), normalize_title(rec.get("title"))), []).append(rec)
        reviewed = sum(1 for r in still_open if (r.get("ai") or {}).get("fit") is not None)
        lines += ["", f"<b>High matches still open, not applied ({len(still_open)})</b>",
                  f"<i>{reviewed} of them reviewed by the AI so far</i>"]
        for same in list(groups.values())[:OPEN_SHOWN]:
            rec = same[0]
            link = rec.get("apply_url") or rec.get("url")
            title = f'<a href="{_esc(link)}">{_esc(rec.get("title"))}</a>' if link else _esc(rec.get("title"))
            codes = " ".join(f"<code>{job_code(r.get('canonical_id'))}</code>" for r in same[:3])
            lines.append("")
            lines.append(f"• {title}" + (f" — {_esc(rec['company'])}" if rec.get("company") else "")
                         + (f" ×{len(same)}" if len(same) > 1 else ""))
            review = next((r["ai"] for r in same if (r.get("ai") or {}).get("fit") is not None), {})
            facts = f"   score {int(rec.get('score') or 0)}" + (f" · 🎯 fit {review['fit']}/10" if review else "") + f" · {codes}"
            lines.append(facts)
            if review.get("summary"):
                lines.append(f"   💡 {_esc(review['summary'])}")
            if review.get("concerns"):
                lines.append(f"   ⚠️ {_esc(review['concerns'])}")
        lines.append("")
        lines.append("/ai shows the AI's view of every match · /applied code · /hide code")
    text = "\n".join(lines)
    return text if len(text) <= 4096 else text[:4095] + "…"
