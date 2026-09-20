"""Deterministic 0-100 relevance scoring with evidence and rejection reasons.

Categories (capped): title 40, technical skills 25, domain 20, responsibilities 10, location 5.
Every bullet in ``why_matched`` corresponds to text that was actually found in the job.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

from ..models import TIER_HIGH, TIER_POSSIBLE, TIER_REJECTED
from ..utils.text import fold
from . import profile as P

# Rejection reason codes
NO_RELEVANT_TITLE = "NO_RELEVANT_TITLE"
NEGATIVE_TITLE = "NEGATIVE_TITLE"
INSUFFICIENT_GEOSPATIAL_SIGNALS = "INSUFFICIENT_GEOSPATIAL_SIGNALS"
LOW_TECHNICAL_RELEVANCE = "LOW_TECHNICAL_RELEVANCE"
LOCATION_MISMATCH = "LOCATION_MISMATCH"
LOW_SCORE = "LOW_SCORE"
WORK_AUTHORIZATION_REQUIRED = "WORK_AUTHORIZATION_REQUIRED"
SPONSORSHIP_EVIDENCE = "Visa sponsorship offered"

SEP = r"[\s\-/&,:|()]+"


@dataclass
class TermHit:
    canonical: str
    weight: int
    family: str | None
    qualifier: str | None = None


@dataclass
class MatchResult:
    score: int
    tier: str
    breakdown: dict
    title_kind: str
    title_evidence: str | None
    skills: list[TermHit] = field(default_factory=list)
    domains: list[TermHit] = field(default_factory=list)
    responsibilities: list[TermHit] = field(default_factory=list)
    geo_families: list[str] = field(default_factory=list)
    why_matched: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    title_only: bool = False

    @property
    def skill_names(self) -> list[str]:
        return [h.canonical for h in self.skills]

    @property
    def domain_names(self) -> list[str]:
        return [h.canonical for h in self.domains]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["skills"] = [asdict(h) for h in self.skills]
        data["domains"] = [asdict(h) for h in self.domains]
        data["responsibilities"] = [asdict(h) for h in self.responsibilities]
        return data


def _phrase_regex(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in re.split(r"[\s/]+", fold(phrase)) if w]
    joiner = SEP + r"(?:[a-z0-9.+]+" + SEP + r"){0,2}"
    return re.compile(r"(?<![a-z0-9])" + joiner.join(words) + r"(?![a-z0-9])")


@lru_cache(maxsize=None)
def _compiled_roles():
    direct = [(role, _phrase_regex(role)) for role in P.DIRECT_ROLES]
    adjacent = [(role, _phrase_regex(role)) for role in P.ADJACENT_ROLES]
    geo_terms = [re.compile(p) for p in P.GEO_TITLE_TERMS]
    role_noun = re.compile(r"\b(?:" + P.ROLE_NOUNS + r")s?\b")
    generic = [re.compile(p) for p in P.GENERIC_TITLE_PATTERNS]
    return direct, adjacent, geo_terms, role_noun, generic


@lru_cache(maxsize=64)
def _compiled_negatives(extra: tuple[str, ...]):
    items = list(P.NEGATIVE_TITLES) + list(extra)
    return [(neg, re.compile(r"(?<![a-z0-9])" + re.escape(fold(neg)) + r"(?![a-z0-9])")) for neg in items]


@lru_cache(maxsize=None)
def _compiled_weak():
    weak_sources = set(P.WEAK_GEO_TITLE_TERMS)
    strong = [re.compile(p) for p in P.GEO_TITLE_TERMS if p not in weak_sources
              and not p.startswith(r"\bsurvey")]  # "survey technician" style terms are weak as well
    weak = [re.compile(p) for p in P.WEAK_GEO_TITLE_TERMS]
    return strong, weak, re.compile(P.SURVEY_EVIDENCE, re.IGNORECASE)


def weak_only_title(title: str) -> bool:
    """True when the title's only geospatial signal is a weak word such as "surveyor" or "mapping"."""
    t = fold(title)
    strong, weak, _ = _compiled_weak()
    return any(rx.search(t) for rx in weak) and not any(rx.search(t) for rx in strong)


@lru_cache(maxsize=None)
def _compiled_work_auth():
    return tuple([re.compile(p) for p in group] for group in (
        P.SPONSORSHIP_REFUSED, P.WORK_AUTH_DEMANDED, P.CITIZENSHIP_RESTRICTED, P.SPONSORSHIP_OFFERED, P.WORK_AUTH_COMPATIBLE))


# A negation counts only when it governs the phrase: at most three plain words between them, inside one sentence or line.
NEGATION_BEFORE_RE = re.compile(r"\b(?:no|not|never|without|unable to|cannot|can not|can.?t|won.?t|will not|does not|do not|don.?t|"
                                r"doesn.?t|isn.?t|aren.?t|ineligible for|pas de|pas|sans|aucune?)\s+(?:[a-z']+\s+){0,3}$")
NEGATION_AFTER_RE = re.compile(r"^\s*(?:(?:is |are |will be |can ?)?(?:not\b|unavailable|n.?est pas|non disponible)|[:?\-]\s*(?:no|non|none|n/a)\b)")
WAIVED_AFTER_RE = re.compile(r"^[^.;!?\n]{0,40}?\b(?:(?:is |are )?not (?:required|needed|necessary|essential|mandatory)|"
                             r"desirable|desired|preferred|advantageous|a plus|an asset|nice to have|beneficial)\b")
INCLUSIVE_AFTER_RE = re.compile(r"^[^.;!?\n]{0,40}?\b(?:and|or|as well as|et|ou)\s+(?:non|other|all|foreign\w*|third|international|expat\w*|candidates who)\b")
INCLUSIVE_BEFORE_RE = re.compile(r"(?:\bnon[- ]|\bregardless of [a-z ]{0,20}|\bnot an? |\bwhether you are an? )$")


def _folded_with_marks(text: str) -> tuple[str, str]:
    """(folded, marks): the same string twice, except that `marks` keeps line and bullet breaks as newlines, so phrase
    patterns run on ordinary spaced text while sentence boundaries stay visible at the same positions."""
    parts = [fold(part) for part in re.split(r"[\n\r\u2022\u25cf\u25aa]+", text or "")]
    parts = [part for part in parts if part]
    return " ".join(parts), "\n".join(parts)


def _sentence_before(marks: str, start: int, span: int = 80) -> str:
    cut = max(marks.rfind(ch, 0, start) for ch in ".;!?:\n")
    return marks[max(cut + 1, start - span, 0):start]


def _negated(folded: str, start: int, end: int, marks: str | None = None) -> bool:
    """Is the phrase at [start, end) denied in its own sentence? ('this position is not eligible for visa support')"""
    marks = marks if marks is not None else folded
    return bool(NEGATION_BEFORE_RE.search(_sentence_before(marks, start)) or NEGATION_AFTER_RE.search(marks[end:end + 40]))


def _waived(marks: str, start: int, end: int) -> bool:
    """A restriction that the sentence itself lifts: 'US citizenship is not required', 'no security clearance is required',
    'DV clearance desirable', 'EU citizens and non-EU nationals alike', 'non-EU nationals'."""
    before = _sentence_before(marks, start)
    after = marks[end:end + 60]
    return bool(NEGATION_BEFORE_RE.search(before) or INCLUSIVE_BEFORE_RE.search(before)
                or WAIVED_AFTER_RE.search(after) or INCLUSIVE_AFTER_RE.search(after))


def work_authorization(text: str, location: dict, cfg: P.MatchConfig) -> tuple[bool, bool]:
    """Return (requires_existing_authorization, sponsorship_offered) for the posting text.

    * a sponsorship refusal always wins: it rejects and cancels any offer wording elsewhere in the posting
    * a demand for an existing right to work, or a citizenship / residency / clearance restriction, rejects unless
      sponsorship is offered or the sentence waives the restriction
    * nothing applies to a job in cfg.home_countries, or when the posting only asks for the right to work in the
      candidate's own country of residence; a refusal to sponsor is irrelevant for a work-from-anywhere role
    """
    refused_rx, demanded_rx, restricted_rx, offered_rx, compatible_rx = _compiled_work_auth()
    folded, marks = _folded_with_marks(text)
    offered = any(not _negated(folded, m.start(), m.end(), marks) for rx in offered_rx for m in rx.finditer(folded))
    refused = any(rx.search(folded) for rx in refused_rx)
    demanded = any(not _waived(marks, m.start(), m.end()) for rx in demanded_rx for m in rx.finditer(folded))
    restricted = any(not _waived(marks, m.start(), m.end()) for rx in restricted_rx for m in rx.finditer(folded))
    compatible = any(rx.search(folded) for rx in compatible_rx)
    country = fold(location.get("country") or "")
    scope = fold(location.get("remote_scope") or "")
    homes = [fold(c) for c in cfg.home_countries if c.strip()]
    at_home = bool(homes) and (country in homes or scope in homes)
    anywhere = bool(location.get("remote")) and scope == "worldwide"
    if refused:
        offered = False
    if at_home or compatible:
        return False, offered
    required = (refused and not anywhere) or ((demanded or restricted) and not offered)
    return required, offered


@lru_cache(maxsize=None)
def _compiled_terms(kind: str):
    source = {"tech": P.TECH_SKILLS, "domain": P.DOMAIN_TERMS, "resp": P.RESPONSIBILITY_TERMS}[kind]
    return [(term, [re.compile(p, re.IGNORECASE | re.DOTALL) for p in term.patterns]) for term in source]


def classify_title(title: str, extra_negatives: tuple[str, ...] = ()) -> tuple[str, int, str | None, str | None]:
    """Return (kind, points, evidence, negative_hit).

    kind: direct | adjacent | geo_title | geo_term | generic | none
    """
    t = fold(title)
    direct, adjacent, geo_terms, role_noun, generic = _compiled_roles()
    has_geo = any(rx.search(t) for rx in geo_terms)

    negative_hit = None
    for neg, rx in _compiled_negatives(tuple(extra_negatives)):
        if rx.search(t):
            if neg in P.NEGATIVE_OVERRIDABLE and has_geo:
                continue
            negative_hit = neg
            break

    for role, rx in direct:
        if rx.search(t):
            return "direct", 40, f"{role} title", negative_hit
    for role, rx in adjacent:
        if rx.search(t):
            return "adjacent", 34, f"{role} title", negative_hit
    if has_geo and role_noun.search(t):
        return "geo_title", 34, f"Geospatial title: {title.strip()}", negative_hit
    if has_geo:
        return "geo_term", 28, f"Geospatial term in title: {title.strip()}", negative_hit
    if any(rx.search(t) for rx in generic):
        return "generic", 12, f"Generic title: {title.strip()}", negative_hit
    return "none", 0, None, negative_hit


def title_prefilter(title: str, geo_context: bool, extra_negatives: tuple[str, ...] = ()) -> bool:
    """Cheap gate used by high-volume sources before fetching descriptions or storing records."""
    kind, _, _, negative = classify_title(title or "", extra_negatives)
    if negative:
        return False
    if kind in ("direct", "adjacent", "geo_title", "geo_term"):
        return True
    return kind == "generic" and geo_context


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(ch, 0, start) for ch in ".\n;•!?")
    rights = [pos for pos in (text.find(ch, end) for ch in ".\n;•!?") if pos != -1]
    right = min(rights) if rights else len(text)
    return text[left + 1: right]


def _qualifier(text: str, start: int, end: int) -> str | None:
    sentence = fold(_sentence_around(text, start, end))
    if re.search(P.PREFERRED_HINTS, sentence):
        return "preferred"
    if re.search(P.REQUIRED_HINTS, sentence):
        return "required"
    return None


def extract_terms(text: str, kind: str) -> list[TermHit]:
    """Extract canonical terms (variants collapse to one canonical name; each counted once)."""
    hits: dict[str, TermHit] = {}
    for term, patterns in _compiled_terms(kind):
        for rx in patterns:
            m = rx.search(text)
            if m:
                hits[term.canonical] = TermHit(term.canonical, term.weight, term.family,
                                               _qualifier(text, m.start(), m.end()) if kind == "tech" else None)
                break
    # context guards
    for term, _ in _compiled_terms(kind):
        if term.requires and term.canonical in hits and term.requires not in hits and not (
            term.requires == "ArcGIS" and "ArcGIS Pro" in hits
        ):
            del hits[term.canonical]
    ordered = sorted(hits.values(), key=lambda h: -h.weight)
    return ordered


def _capped(hits: list[TermHit], cap: int) -> int:
    return min(cap, sum(h.weight for h in hits))


def _location_points(location: dict, cfg: P.MatchConfig) -> tuple[int, str | None, bool]:
    """Return (points, evidence, mismatch)."""
    remote = location.get("remote")
    scope = location.get("remote_scope")
    mode = location.get("work_mode")
    place = " ".join(fold(location.get(k) or "") for k in ("city", "region", "country", "raw"))
    prefs = [fold(p) for p in cfg.preferred_locations if p.strip()]
    scopes = [fold(s) for s in cfg.accepted_remote_scopes if s.strip()]

    if remote:
        if scope and (fold(scope) == "worldwide" or fold(scope) in scopes or any(p in fold(scope) for p in prefs)):
            return 5, f"Remote — {scope}", False
        if scope is None:
            return 3, "Remote (scope not stated)", False
        if scopes or prefs:
            return 1, f"Remote — {scope}", True
        return 4, f"Remote — {scope}", False
    if prefs:
        if any(p and p in place for p in prefs):
            return 5, "Preferred location", False
        if not place.strip():
            return 1, None, False
        return 0, None, True
    if mode == "hybrid":
        return 3, None, False
    return 2, None, False


def score_job(title: str, description: str, location: dict, cfg: P.MatchConfig | None = None) -> MatchResult:
    cfg = cfg or P.MatchConfig()
    description = description or ""
    kind, title_points, title_evidence, negative_hit = classify_title(title or "", tuple(cfg.extra_negative_titles))
    full_text = f"{title or ''}\n{description}"

    skills = extract_terms(full_text, "tech")
    domains = extract_terms(full_text, "domain")
    resp = extract_terms(full_text, "resp")
    families = sorted({h.family for h in skills + domains + resp if h.family})

    tech_points = _capped(skills, P.CATEGORY_CAPS["tech"])
    domain_points = _capped(domains, P.CATEGORY_CAPS["domain"])
    resp_points = _capped(resp, P.CATEGORY_CAPS["responsibilities"])
    loc_points, loc_evidence, loc_mismatch = _location_points(location or {}, cfg)

    rejections: list[str] = []
    if negative_hit:
        rejections.append(NEGATIVE_TITLE)
    if kind == "none":
        rejections.append(NO_RELEVANT_TITLE)
    if kind == "generic" and len(families) < P.MIN_GEO_SIGNALS:
        rejections.append(INSUFFICIENT_GEOSPATIAL_SIGNALS)
        title_points = 0
    if loc_mismatch and cfg.strict_location:
        rejections.append(LOCATION_MISMATCH)
    auth_required, sponsorship = work_authorization(full_text, location or {}, cfg)
    if auth_required and cfg.exclude_work_auth_required:
        rejections.append(WORK_AUTHORIZATION_REQUIRED)
    if sponsorship:  # an employer that sponsors makes any location reachable
        loc_points, loc_mismatch = P.CATEGORY_CAPS["location"], False

    score = title_points + tech_points + domain_points + resp_points + loc_points
    title_only = False
    has_description = len(description.strip()) >= 300
    weak_title = kind != "none" and weak_only_title(title or "")
    if weak_title and has_description:
        # "Surveyor" / "Mapping" titles need geomatics evidence in the body: another geospatial family, a
        # geospatial tool, or concrete land-survey vocabulary (GNSS, total station, cadastral survey...).
        body_hits = extract_terms(description, "tech") + extract_terms(description, "domain") + extract_terms(description, "resp")
        body_families = {h.family for h in body_hits if h.family and h.family != "surveying"}
        if not body_families and not _compiled_weak()[2].search(description):
            rejections.append(INSUFFICIENT_GEOSPATIAL_SIGNALS)
    floor_allowed = not weak_title or kind in ("direct", "adjacent")
    if not has_description and not rejections and floor_allowed and title_points >= cfg.title_only_min_points:
        if score < cfg.medium_threshold:
            score = cfg.medium_threshold
            title_only = True
    score = max(0, min(100, score))

    if rejections:
        tier = TIER_REJECTED
    elif score >= cfg.high_threshold:
        tier = TIER_HIGH
    elif score >= cfg.medium_threshold:
        tier = TIER_POSSIBLE
    else:
        tier = TIER_REJECTED
        rejections.append(LOW_SCORE)
    if tier == TIER_REJECTED and tech_points == 0 and has_description:
        rejections.append(LOW_TECHNICAL_RELEVANCE)
    if tier == TIER_REJECTED and loc_mismatch and LOCATION_MISMATCH not in rejections:
        rejections.append(LOCATION_MISMATCH)

    why: list[str] = []
    if title_evidence and title_points:
        why.append(title_evidence)
    for hit in skills:
        why.append(f"{hit.canonical} {hit.qualifier}" if hit.qualifier else hit.canonical)
    for hit in domains:
        if hit.canonical not in ("GIS", "Geospatial") or not title_points:
            why.append(f"{hit.canonical} domain")
    for hit in resp:
        why.append(hit.canonical)
    if loc_evidence:
        why.append(loc_evidence)
    if sponsorship:
        why.append(SPONSORSHIP_EVIDENCE)
    if title_only:
        why.append("Title-only evidence (source provided no description)")

    return MatchResult(
        score=score,
        tier=tier,
        breakdown={
            "title": title_points, "tech": tech_points, "domain": domain_points,
            "responsibilities": resp_points, "location": loc_points,
        },
        title_kind=kind,
        title_evidence=title_evidence,
        skills=skills,
        domains=domains,
        responsibilities=resp,
        geo_families=families,
        why_matched=why,
        rejection_reasons=list(dict.fromkeys(rejections)),
        title_only=title_only,
    )
