"""Official public employment-service APIs.

  bundesagentur   Germany's Bundesagentur für Arbeit job search. Public API with a fixed, published key; no sign-up.
                  The list endpoint (v6) has no description, so postings whose title passes the prefilter get one
                  detail request each (v4), capped per run. Most postings are written in German, which the
                  candidate does not speak: only postings written mostly in English or French are kept, and the
                  others are remembered so their details are not fetched again.
  francetravail   France Travail (ex Pôle emploi) "Offres d'emploi v2". Free, but needs an application registered at
                  francetravail.io (FRANCETRAVAIL_CLIENT_ID / FRANCETRAVAIL_CLIENT_SECRET). Full descriptions.
                  French is one of the candidate's languages, and "Géomètre" is on the France–Tunisia list of
                  occupations open without the labour-market test (see config/visa_paths.toml).
"""
from __future__ import annotations

import base64
import logging
import re

from ..models import BackendOutput, RawJob
from ..utils.dates import parse_datetime
from ..utils.http import FetchError
from ..utils.text import fold, html_to_text
from .base import Backend, RunContext
from .feeds import days_window, rotating_batch

log = logging.getLogger(__name__)

GERMAN_WORDS = {"und", "der", "die", "das", "wir", "sie", "mit", "fuer", "für", "ihre", "ihr", "eine", "einen", "bei", "von",
                "sind", "oder", "auf", "zur", "zum", "sowie", "im", "den", "des", "unser", "unsere", "ist", "nach"}
OTHER_WORDS = {"the", "and", "with", "you", "our", "for", "will", "are", "your", "of", "to", "in", "we", "is", "as",
               "les", "des", "et", "vous", "nous", "pour", "avec", "dans", "une", "sur", "est", "du", "en", "la", "le"}
MAX_REMEMBERED = 3000


def mostly_german(text: str) -> bool:
    """True when the function words of a posting are predominantly German."""
    words = [w.strip(".,;:()!?/").lower() for w in (text or "")[:2500].split()]
    german = sum(w in GERMAN_WORDS for w in words)
    other = sum(w in OTHER_WORDS for w in words)
    return german >= 8 and german > other


class BundesagenturBackend(Backend):
    name = "bundesagentur"
    phase = "extraction"
    source_type = "government"
    min_interval_hours = 6
    api = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc"
    headers = {"X-API-Key": "jobboerse-jobsuche", "Accept": "application/json"}  # the key is public and documented
    terms = ["GIS", "Geospatial", "Geoinformatik", "Remote Sensing", "LiDAR", "Photogrammetrie", "Geodaten", "Vermessung GIS"]
    queries_per_run = 2
    details_per_run = 15
    page_size = 100
    pages_per_term = 4

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, seen, details, german = [], 0, set(), 0, 0
        cursor = ctx.cursor(self.name)
        remembered = list(cursor.get("german_refs") or [])  # oldest first: the list is trimmed by age, not by spelling
        skip = set(remembered)
        batch = rotating_batch(ctx, self.name, [("de", term) for term in self.terms], self.queries_per_run)
        for term in batch.get("de", []):
            if ctx.out_of_time(200):
                break
            items, failed = [], False
            for page in range(1, self.pages_per_term + 1):  # the API serves results by relevance: read them all, not the top 50
                try:
                    data = ctx.client.get(f"{self.api}/v6/jobs", params={"was": term, "angebotsart": 1, "size": self.page_size, "page": page,
                                                                          "veroeffentlichtseit": min(100, days_window(ctx.settings))},
                                          headers=self.headers, respect_robots=False, detect_challenge=False).json()
                except FetchError as exc:
                    errors.append(f"{term}: {exc.kind}")
                    failed = page == 1
                    break
                except ValueError:
                    errors.append(f"{term}: non-JSON response")
                    failed = page == 1
                    break
                rows = data.get("ergebnisliste") if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    if page == 1:
                        errors.append(f"{term}: unexpected structure")
                        failed = True
                    break
                items.extend(rows)
                total = int((data.get("maxErgebnisse") if isinstance(data, dict) else 0) or 0)
                if len(rows) < self.page_size or page * self.page_size >= total or ctx.out_of_time(200):
                    break
            if failed:
                continue
            ok += 1
            # newest first: the detail budget (needed to tell the language) goes to what was published last
            items.sort(key=lambda it: str((it or {}).get("aktuelleVeroeffentlichungsdatum") or (it or {}).get("eintrittsdatum") or ""), reverse=True)
            for item in items:
                ref = item.get("referenznummer") if isinstance(item, dict) else None
                title = (item or {}).get("stellenangebotsTitel")
                if not ref or not title or ref in seen:
                    continue
                seen.add(ref)
                if ref in skip:
                    german += 1
                    continue
                if not ctx.prefilter(title, True):
                    out.prefiltered_out += 1
                    continue
                if ctx.knows_text(f"bundesagentur:{ref}"):
                    out.jobs.append(self.to_raw(item))  # read before and not German: still open, its text is on file
                    continue
                if details >= self.details_per_run or ctx.out_of_time(200):
                    continue  # without the text the language is unknown: next run
                details += 1
                description = self._description(ctx, ref)
                if not description:
                    continue
                if mostly_german(description):
                    skip.add(ref)
                    remembered.append(ref)
                    german += 1
                    continue
                try:
                    out.jobs.append(self.to_raw(item, description))
                except Exception:
                    ctx.record_parser_error(self.name)
        cursor["german_refs"] = remembered[-MAX_REMEMBERED:]
        out.details = {"queries_ok": ok, "detail_requests": details, "german_language_skipped": german, "errors": errors[:10]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0 and errors:
            out.status, out.error = "FAILED", errors[0]
        elif errors:
            out.status = "PARTIAL"
        return out

    def _description(self, ctx: RunContext, ref: str) -> str:
        token = base64.b64encode(ref.encode("utf-8")).decode("ascii")
        try:
            data = ctx.client.get(f"{self.api}/v4/jobdetails/{token}", headers=self.headers, respect_robots=False,
                                  detect_challenge=False).json()
        except (FetchError, ValueError):
            return ""
        return html_to_text(data.get("stellenangebotsBeschreibung")) if isinstance(data, dict) else ""

    @staticmethod
    def to_raw(item: dict, description: str = "") -> RawJob:
        ref = item["referenznummer"]
        place = ((item.get("stellenlokationen") or [{}])[0] or {}).get("adresse") or {}
        location = ", ".join(p for p in (place.get("ort"), (place.get("region") or "").replace("_", " ").title(), "Germany") if p)
        salary = None
        if item.get("gehaltsspanneVon") or item.get("gehaltsspanneBis"):
            period = {"JAHRESGEHALT": "yearly", "MONATSGEHALT": "monthly", "STUNDENLOHN": "hourly"}.get(item.get("verguetungsangabe"), "")
            salary = f"EUR {item.get('gehaltsspanneVon') or ''}–{item.get('gehaltsspanneBis') or ''} {period}".strip()
        posted = parse_datetime(item.get("datumErsteVeroeffentlichung") or (item.get("veroeffentlichungszeitraum") or {}).get("von"))
        url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{ref}"
        return RawJob(
            source_type="government", source_name="bundesagentur", source_url="https://rest.arbeitsagentur.de/jobboerse/jobsuche-service",
            title=html_to_text(item.get("stellenangebotsTitel")), company=item.get("firma") or None, url=url,
            apply_url=item.get("externeURL") or url, description=description, location_raw=location,
            remote_flag=True if item.get("homeofficemoeglich") and (item.get("homeofficeprozent") or 0) >= 90 else None,
            employment_type="Full-time" if item.get("arbeitszeitVollzeit") else None, salary=salary, posted_at=posted,
            posted_at_reliable=posted is not None, source_job_id=f"bundesagentur:{ref}", extraction_method="api", geo_context=True,
        )


FRENCH_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7, "aout": 8, "septembre": 9,
                 "octobre": 10, "novembre": 11, "decembre": 12}


def _french_date(text: str):
    """"17 septembre 2026" -> datetime (UTC), or None."""
    from datetime import datetime, timezone

    from ..utils.text import fold

    match = re.search(r"(\d{1,2})(?:er)? ([a-z]+) (20\d{2})", fold(text))
    if not match or match.group(2) not in FRENCH_MONTHS:
        return None
    try:
        return datetime(int(match.group(3)), FRENCH_MONTHS[match.group(2)], int(match.group(1)), tzinfo=timezone.utc)
    except ValueError:
        return None


class JobBankBackend(Backend):
    """Canada's Job Bank and its French twin, Guichet-Emplois, read from their search pages.

    Their Atom feeds still answer, but with no entries, for every query; the search pages list the same postings
    (robots.txt asks for a five-second crawl delay and forbids nothing). A result gives title, employer, place, pay and
    date; the posting page is queued for the page extractor, which reads its RDFa markup for the full text.
    """

    name = "jobbank"
    phase = "discovery"  # it queues posting pages, which are read in the extraction phase of the same run
    source_type = "government"
    min_interval_hours = 6
    sites = {"en": "https://www.jobbank.gc.ca", "fr": "https://www.guichetemplois.gc.ca"}
    terms = [("en", "geomatics"), ("en", "GIS"), ("en", "land surveyor"), ("en", "survey technician"), ("en", "remote sensing"),
             ("en", "cartographer"), ("en", "LiDAR"), ("fr", "géomatique"), ("fr", "arpenteur"), ("fr", "cartographe"), ("fr", "SIG")]
    weak_terms = {"land surveyor", "survey technician", "arpenteur"}
    queries_per_run = 3

    @staticmethod
    def parse(html: str, origin: str) -> list[RawJob]:
        from bs4 import BeautifulSoup

        jobs = []
        for article in BeautifulSoup(html or "", "html.parser").select('article[id^="article-"]'):
            number = article.get("id", "").split("-", 1)[1]
            title_node = article.select_one(".noctitle")
            if not number.isdigit() or title_node is None:
                continue
            text = lambda cls: " ".join((article.select_one(f"li.{cls}").get_text(" ") if article.select_one(f"li.{cls}") else "").split())  # noqa: E731
            place = re.sub(r"^(?:Location|Emplacement|Lieu)\s*", "", text("location"))  # "Burlington (ON)"
            place = re.sub(r"\s*\(([A-Z]{2})\)$", r", \1", place)
            salary = re.sub(r"^(?:Salary|Salaire)\s*:?\s*", "", text("salary")) or None
            posted = parse_datetime(text("date")) or _french_date(text("date"))
            url = f"{origin}/jobsearch/jobposting/{number}"
            jobs.append(RawJob(
                source_type="government", source_name="jobbank", source_url=f"{origin}/jobsearch/jobsearch", url=url, apply_url=url,
                title=" ".join(title_node.get_text(" ").split()), company=text("business") or None,
                location_raw=", ".join(p for p in (place, "Canada") if p), salary=salary, posted_at=posted,
                posted_at_reliable=posted is not None, source_job_id=f"jobbank:{number}", extraction_method="html", geo_context=True,
            ))
        return jobs

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        errors, ok, queued, seen = [], 0, 0, set()
        batch = rotating_batch(ctx, self.name, self.terms, self.queries_per_run)
        for language, terms in batch.items():
            origin = self.sites[language]
            for term in terms:
                if ctx.out_of_time(300):
                    break
                try:
                    page = ctx.client.get(f"{origin}/jobsearch/jobsearch", params={"searchstring": term, "sort": "D"}).text
                except FetchError as exc:
                    errors.append(f"{term}: {exc.kind}")
                    continue
                ok += 1
                for job in self.parse(page, origin):
                    if job.source_job_id in seen:
                        continue
                    seen.add(job.source_job_id)
                    job.geo_context = fold(term) not in self.weak_terms  # "surveyor" also finds quantity and marine surveyors
                    if not ctx.prefilter(job.title, job.geo_context):
                        out.prefiltered_out += 1
                        continue
                    out.jobs.append(job)
                    if not ctx.knows_text(job.source_job_id) and ctx.pages.add(job.url, origin="jobbank", priority=80,
                                                                               source_type="government", geo_context=job.geo_context):
                        queued += 1
        out.details = {"queries_ok": ok, "pages_queued": queued, "errors": errors[:10]}
        if ok == 0 and errors:
            out.status, out.error = "FAILED", errors[0]
        elif errors:
            out.status = "PARTIAL"
        return out


class FranceTravailBackend(Backend):
    name = "francetravail"
    phase = "extraction"
    source_type = "government"
    min_interval_hours = 4
    token_url = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    api = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
    terms = ["géomatique", "SIG", "géomaticien", "cartographe", "topographe", "géomètre", "télédétection", "LiDAR",
             "photogrammétrie", "GIS"]
    queries_per_run = 4
    WINDOWS = (1, 3, 7, 14, 31)  # the only values publieeDepuis accepts

    def enabled(self, ctx):
        ok, reason = super().enabled(ctx)
        if ok and not (ctx.settings.francetravail_client_id and ctx.settings.francetravail_client_secret):
            return False, "FRANCETRAVAIL_CLIENT_ID / FRANCETRAVAIL_CLIENT_SECRET not set"
        return ok, reason

    def _token(self, ctx: RunContext) -> str:
        response = ctx.client.post(self.token_url, params={"realm": "/partenaire"},
                                   data={"grant_type": "client_credentials", "client_id": ctx.settings.francetravail_client_id,
                                         "client_secret": ctx.settings.francetravail_client_secret,
                                         "scope": "api_offresdemploiv2 o2dsoffre"},
                                   respect_robots=False, detect_challenge=False)
        token = (response.json() or {}).get("access_token")
        if not token:
            raise ValueError("no access_token in the answer")
        return token

    def run(self, ctx: RunContext) -> BackendOutput:
        out = BackendOutput()
        try:
            token = self._token(ctx)
        except FetchError as exc:
            out.status, out.error = "FAILED", f"token: {exc.kind}"  # never the body: it may echo credentials
            return out
        except ValueError as exc:
            out.status, out.error = "FAILED", f"token: {exc}"
            return out
        wanted = days_window(ctx.settings)
        window = next((w for w in self.WINDOWS if w >= wanted), 31)
        errors, ok, seen = [], 0, set()
        batch = rotating_batch(ctx, self.name, [("fr", term) for term in self.terms], self.queries_per_run)
        for term in batch.get("fr", []):
            if ctx.out_of_time(200):
                break
            try:
                response = ctx.client.get(self.api, params={"motsCles": term, "publieeDepuis": window, "range": "0-99", "sort": 1},  # 1 = newest first; the default is "relevance"
                                          headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                          respect_robots=False, detect_challenge=False)
                data = response.json() if response.status_code != 204 and response.content else {"resultats": []}
            except FetchError as exc:
                errors.append(f"{term}: {exc.kind}")
                if exc.kind in ("AUTH_REQUIRED", "BLOCKED", "RATE_LIMITED"):
                    break
                continue
            except ValueError:
                errors.append(f"{term}: non-JSON response")
                continue
            items = data.get("resultats") if isinstance(data, dict) else None
            if not isinstance(items, list):
                errors.append(f"{term}: unexpected structure")
                continue
            ok += 1
            for item in items:
                if not isinstance(item, dict) or not item.get("id") or item["id"] in seen:
                    continue
                seen.add(item["id"])
                try:
                    job = self.to_raw(item)
                except Exception:
                    ctx.record_parser_error(self.name)
                    continue
                if job.title and ctx.prefilter(job.title, True):
                    out.jobs.append(job)
                else:
                    out.prefiltered_out += 1
        out.details = {"queries_ok": ok, "errors": errors[:10]}
        if out.jobs:
            ctx.snapshot(self.name, [j.snapshot() for j in out.jobs])
        if ok == 0:
            out.status, out.error = "FAILED", errors[0] if errors else "no successful requests"
        elif errors:
            out.status = "PARTIAL"
        return out

    @staticmethod
    def to_raw(item: dict) -> RawJob:
        place = (item.get("lieuTravail") or {}).get("libelle") or ""  # "75 - PARIS 08", "34 - Montpellier"
        city = place.split(" - ", 1)[1] if " - " in place else place
        origin = (item.get("origineOffre") or {}).get("urlOrigine")
        url = origin or f"https://candidat.francetravail.fr/offres/recherche/detail/{item['id']}"
        posted = parse_datetime(item.get("dateCreation"))
        contract = " · ".join(x for x in (item.get("typeContratLibelle"), item.get("dureeTravailLibelleConverti")) if x)
        return RawJob(
            source_type="government", source_name="francetravail", source_url="https://api.francetravail.io",
            title=html_to_text(item.get("intitule")), company=(item.get("entreprise") or {}).get("nom") or None, url=url,
            apply_url=(item.get("contact") or {}).get("urlPostulation") or url, description=html_to_text(item.get("description")),
            location_raw=f"{city.title()}, France".strip(", "), employment_type=contract or None,
            salary=(item.get("salaire") or {}).get("libelle") or None, posted_at=posted, posted_at_reliable=posted is not None,
            source_job_id=f"francetravail:{item['id']}", extraction_method="api", geo_context=True,
        )
