"""Keyless public APIs: freehire jobs, salaries in euros, EU surveying and mapping tenders."""
from datetime import timedelta

from conftest import GIS_DESCRIPTION, NOW, FakeResponse, FakeSession, make_ctx
from test_commands import job

from geojobbot.insights import fx, signals
from geojobbot.notifications.telegram import format_digest
from geojobbot.scrapers.aggregators import FreehireBackend


def fh(slug, title, **kw):
    base = {"public_slug": slug, "title": title, "company": "Arup", "source": "greenhouse", "location": "Edinburgh", "countries": ["gb"],
            "url": f"https://boards.greenhouse.io/arup/jobs/{slug}?utm_source=freehire.me", "description": f"<p>{GIS_DESCRIPTION}</p>",
            "work_mode": "hybrid", "posted_at": NOW.isoformat(), "closed_at": None,
            "enrichment": {"salary_min": 45000, "salary_max": 55000, "salary_currency": "GBP", "salary_period": "year", "visa_sponsorship": True}}
    base.update(kw)
    return base


def test_freehire_jobs_are_read_and_its_own_visa_field_is_not_trusted():
    api = FreehireBackend.api
    payload = {"data": [fh("a1", "Geospatial Data Engineer"), fh("a2", "Office Manager"),
                        fh("a3", "GIS Analyst", posted_at=(NOW - timedelta(days=60)).isoformat()), fh("a4", "GIS Technician", closed_at=NOW.isoformat())],
               "meta": {"total": 4}}
    out = FreehireBackend().run(make_ctx(FakeSession({api: FakeResponse(200, payload)})))
    assert out.status == "SUCCESS" and [j.title for j in out.jobs] == ["Geospatial Data Engineer"]
    job_ = out.jobs[0]
    assert job_.location_raw == "Edinburgh, United Kingdom" and job_.salary == "GBP 45000–55000 year"
    assert job_.url == "https://boards.greenhouse.io/arup/jobs/a1" and job_.source_name == "freehire:greenhouse"
    assert "sponsor" not in job_.description.lower()  # only the posting's own text decides about sponsorship
    assert out.details["older_than_window"] == 1 and out.prefiltered_out == 1
    ignored = {"data": [fh("b1", "Anything")], "meta": {"ignored_params": [{"param": "q"}]}}
    out = FreehireBackend().run(make_ctx(FakeSession({api: FakeResponse(200, ignored)})))
    assert out.status == "FAILED" and "ignored" in out.error and out.jobs == []  # an unfiltered dump is never taken for a result


def test_salaries_are_shown_in_euros_a_year():
    rates = {"gbp": 0.86, "cad": 1.6, "sar": 4.3, "usd": 1.15}
    assert fx.yearly_eur("GBP 43000–51600 yearly", "United Kingdom", rates) == (50000, 60000)
    assert fx.yearly_eur("$40 an hour", "Canada", rates) == (52000, 52000)
    assert fx.yearly_eur("SAR 18000 monthly", "Saudi Arabia", rates) == (50233, 50233)
    assert fx.yearly_eur("EUR 50000 yearly", "Germany", rates) is None and fx.yearly_eur("EUR 4000 monthly", "France", rates) == (48000, 48000)
    assert fx.yearly_eur("Competitive", "France", rates) is None and fx.yearly_eur("GBP 40000", "United Kingdom", None) is None
    rec = job("s:1", "GIS Analyst", country="United Kingdom", salary="GBP 43000–51600 yearly", last_seen=NOW.isoformat())
    assert fx.annotate(rec, rates) and fx.note(rec) == "≈ €50–60k/yr"
    assert "💰 GBP 43000–51600 yearly (≈ €50–60k/yr)" in format_digest([rec], now=NOW)[0][0]
    state = {}
    session = FakeSession({fx.RATES_URL: FakeResponse(200, {"date": "2026-09-20", "eur": {"usd": 1.15, "gbp": 0.86, "cad": 1.6, "aud": 1.6, "sar": 4.3, "tnd": 3.4}})})
    ctx = make_ctx(session, state=state)
    assert fx.refresh(ctx, state) and state["fx"]["eur"]["sar"] == 4.3
    assert fx.refresh(ctx, state) and len(session.calls) == 1  # once a day


def tender(tid, country, cpv, title="France – Surveying services – Levés topographiques et bathymétriques", **kw):
    return dict({"id": tid, "country": country, "cpv_main": cpv, "cpv_main_label": "Surveying services", "title": title, "buyer": "Ville de Lyon",
                 "published": "2026-09-18", "deadline": "2026-10-20T12:00:00+02:00", "ted_url": f"https://ted.europa.eu/en/notice/-/detail/{tid}"}, **kw)


def test_eu_tenders_start_quietly_then_report_only_new_french_speaking_ones():
    first = [tender(f"{i}-2026", "FRA", "71355000") for i in range(10)] + [tender("de-1", "DEU", "71355000"), tender("fr-arch", "FRA", "71221000")]
    state = {"boards": {}, "cursors": {}, "page_cache": {}}
    ctx = make_ctx(FakeSession({signals.EU_TENDERS: FakeResponse(200, first)}), state=state)
    new = signals.check(ctx, state)
    assert len(new) == 3 and all(s["kind"] == "tender" and s["country"] == "France" for s in new)  # a gentle start, not a backlog of ten
    assert new[0]["title"].startswith("Levés topographiques") and "Ville de Lyon" in new[0]["project"] and "closes 2026-10-20" in new[0]["project"]
    later = first + [tender("new-1", "BEL", "71354000", title="Belgium – Map-making services – Cartographie numérique du réseau")]
    state["signals"]["checked_at"] = None
    ctx = make_ctx(FakeSession({signals.EU_TENDERS: FakeResponse(200, later)}), state=state)
    new = signals.check(ctx, state)
    assert [s["id"] for s in new] == ["ted:new-1"] and new[0]["country"] == "Belgium"  # German and architecture notices never appear
    assert "EU public tenders" in signals.format_signals(new)
