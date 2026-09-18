"""Learn from what the user does: applications are positive labels, hidden jobs negative ones.

Deliberately small and transparent: no model file, no black box. Each feature of a job (title words, skills,
domains, employer, country) gets a weight from how often it appeared in applied versus hidden jobs, and a job's
score is nudged by the sum, clamped to [-8, +6]. /learning shows the weights, /why shows a job's adjustment,
/learning off disables it and /learning reset forgets everything learned so far.
"""
from __future__ import annotations

from collections import Counter

from ..utils.dates import parse_datetime
from ..utils.text import normalize_company, normalize_title
from .sponsors import retier

MIN_LABELS = 4          # nothing is learned from fewer actions than this
MIN_SUPPORT = 2         # a feature must have been seen in at least this many labelled jobs
SCALE = 6.0
MAX_UP, MAX_DOWN = 6, -8
POSITIVE_WEIGHT = {"applied": 1, "interview": 2, "offer": 2, "rejected": 1, "ghosted": 1, "withdrawn": 0}
TITLE_STOP = set("a an and at de des du en et for h f in la le les m of or pour the to un une w d with sr jr ii iii iv 1 2 3".split())


def features(rec: dict) -> set[str]:
    out = set()
    for token in normalize_title(rec.get("title")).split():
        if len(token) > 2 and token not in TITLE_STOP and not token.isdigit():
            out.add(f"word:{token}")
    company = normalize_company(rec.get("company"))
    if company:
        out.add(f"company:{company}")
    if rec.get("country"):
        out.add(f"country:{rec['country']}")
    for skill in rec.get("matched_skills") or rec.get("skills") or []:
        out.add("skill:" + str(skill).split(" (")[0])
    for domain in rec.get("matched_domains") or rec.get("domains") or []:
        out.add(f"domain:{domain}")
    return out


def snapshot(rec: dict) -> dict:
    """What is kept about a labelled job, so learning survives the job being pruned from the state."""
    return {"title": rec.get("title"), "company": rec.get("company"), "country": rec.get("country"),
            "skills": [str(s).split(" (")[0] for s in rec.get("matched_skills") or []][:12],
            "domains": list(rec.get("matched_domains") or [])[:12]}


def build_model(prefs: dict, jobs: dict) -> dict:
    """{feature: weight} learned from the user's applications and hidden jobs (empty when off or too few)."""
    if prefs.get("learning") is False:
        return {}
    since = parse_datetime(prefs.get("learning_since"))
    positive, negative = Counter(), Counter()
    labels = 0
    for cid, info in (prefs.get("applied") or {}).items():
        when = parse_datetime(info.get("at"))
        if since and when and when < since:
            continue
        weight = POSITIVE_WEIGHT.get(info.get("status") or "applied", 1)
        if not weight:
            continue
        labels += 1
        for feature in features({**info, **(jobs.get(cid) or {})} if jobs.get(cid) else info):
            positive[feature] += weight
    hidden_info = prefs.get("hidden_info") or {}
    for cid in prefs.get("hidden") or []:
        info = hidden_info.get(cid) or {}
        when = parse_datetime(info.get("at"))
        if since and when and when < since:
            continue
        source = jobs.get(cid) or info
        if not source:
            continue
        labels += 1
        for feature in features(source):
            negative[feature] += 1
    if labels < MIN_LABELS:
        return {}
    model = {}
    for feature in set(positive) | set(negative):
        pos, neg = positive[feature], negative[feature]
        if pos + neg < MIN_SUPPORT:
            continue
        weight = round(SCALE * (pos - neg) / (pos + neg + 2), 1)
        if abs(weight) >= 1:
            model[feature] = weight
    return model


def adjustment(rec: dict, model: dict) -> tuple[int, list[str]]:
    hits = sorted(((model[f], f) for f in features(rec) if f in model), key=lambda pair: -abs(pair[0]))
    total = sum(weight for weight, _ in hits)
    adj = int(round(max(MAX_DOWN, min(MAX_UP, total))))
    because = [f"{'+' if w > 0 else '−'}{abs(w):g} {f.split(':', 1)[1]}" for w, f in hits[:4]]
    return adj, because


def apply_learning(rec: dict, model: dict, settings) -> int:
    """Nudge a stored job's score once per scoring (same idempotency rule as the sponsor bonus)."""
    breakdown = rec.setdefault("score_breakdown", {})
    if "learned" in breakdown:  # carried over from an earlier run: the job was not rescored since
        return 0
    if not model or set(rec.get("rejection_reasons") or []) - {"LOW_SCORE"}:
        rec.pop("learned", None)
        return 0
    adj, because = adjustment(rec, model)
    if not adj:
        rec.pop("learned", None)
        return 0
    breakdown["learned"] = adj
    rec["learned"] = {"adj": adj, "because": because}
    rec["score"] = max(0, min(100, int(rec.get("score") or 0) + adj))
    retier(rec, settings)
    return adj


def describe_model(model: dict, limit: int = 8) -> tuple[list[str], list[str]]:
    ordered = sorted(model.items(), key=lambda kv: -kv[1])
    liked = [f"+{w:g} {f.split(':', 1)[1]} ({f.split(':', 1)[0]})" for f, w in ordered if w > 0][:limit]
    disliked = [f"−{abs(w):g} {f.split(':', 1)[1]} ({f.split(':', 1)[0]})" for f, w in reversed(ordered) if w < 0][:limit]
    return liked, disliked
