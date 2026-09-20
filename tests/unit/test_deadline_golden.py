"""Golden table for deadline reading. A wrong past date hides a good job (DEADLINE_PASSED), so false positives matter most."""
from datetime import date, datetime, timezone

import pytest

from geojobbot.insights import timing

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

NOT_A_DEADLINE = [
    ("This vacancy closes at midnight. Posted 01/09/2026", None),
    ("Closing date: Ongoing. Date posted: 01/09/2026", None),
    ("Closing Date: Open until filled Posted Date: 09/01/2026", "United States"),
    ("Open until filled. Posted 09/01/2026.", "United States"),
    ("Advert may close early. Posted: 2 Sept 2026", None),
    ("Apply by email. Start date 1 September 2026.", None),
    ("Apply by sending your CV; start 1 Sept 2026", None),
    ("Poste à pourvoir avant le 1er septembre 2026.", "France"),
    ("Démarrage souhaité avant le 15 septembre 2026", "France"),
    ("CDD de remplacement jusqu'au 15/09/2026, renouvelable", "France"),
    ("CDD jusqu'au 31 décembre 2026", "France"),
    ("The project closes on 31 August 2026", None),
    ("We expect the successful candidate to submit by 1 September 2026 their first report", None),
    ("Our office closes on 24 December and reopens in January.", None),
    ("The project runs until 31 December 2029.", None),
    ("We were founded on 3 March 1999. Open until filled.", None),
    ("Start date: 5 October 2026", None),
    ("Closing date: 10/08/2026", "Canada"),   # 10 August or 8 October? one is past, one is not: never hide a job on a guess
    ("Apply by 10/09/2026", None),
]
DEADLINES = [
    ("Closing date: 30 September 2026 at 5pm", None, date(2026, 9, 30)),
    ("Applications close at 5pm on 30 September 2026", None, date(2026, 9, 30)),
    ("Date limite de candidature : 05/10/2026", "France", date(2026, 10, 5)),
    ("Bewerbungsfrist: 30.09.2026", "Germany", date(2026, 9, 30)),
    ("Closes: 4 Oct 2026", None, date(2026, 10, 4)),
    ("Some text about the team.\nCloses: 4 Oct 2026", None, date(2026, 10, 4)),
    ("This vacancy closes on 12 October 2026.", None, date(2026, 10, 12)),
    ("Please apply by Oct 2nd.", None, date(2026, 10, 2)),
    ("Applications close 09/30/2026", "United States", date(2026, 9, 30)),
    ("Application deadline: 2026-10-12", None, date(2026, 10, 12)),
    ("Candidatures jusqu'au 1er octobre 2026", "France", date(2026, 10, 1)),
    ("Merci d'envoyer votre CV avant le 15 octobre 2026.", "France", date(2026, 10, 15)),
    ("<p>Closing Date:</p><p>25 Sept 2026</p>", None, date(2026, 9, 25)),
    ("Posting End Date: October 15, 2026", "Canada", date(2026, 10, 15)),
    ("Closing date: 11/12/2026", "Canada", date(2026, 11, 12)),  # both readings are in the future: the earlier one is kept
    ("Closing date: 11/10/2026", "United Kingdom", date(2026, 10, 11)),
]


@pytest.mark.parametrize("text,country", NOT_A_DEADLINE)
def test_not_a_deadline(text, country):
    assert timing.find_deadline(text, NOW, country) is None, text


@pytest.mark.parametrize("text,country,expected", DEADLINES)
def test_deadlines(text, country, expected):
    assert timing.find_deadline(text, NOW, country) == expected, text


def test_a_stored_deadline_is_replaced_or_cleared_by_the_text_and_the_ai_fills_in():
    long = "We are hiring a GIS analyst to join our mapping team. " * 6
    rec = {"deadline": "2026-09-01", "deadline_reminded": True, "country": "France"}
    assert timing.annotate_deadline(rec, long + "No deadline here, rolling recruitment.", NOW) is False
    assert "deadline" not in rec and rec["deadline_reminded"] is False  # a date read by an older rule must not keep hiding the job
    rec = {"deadline": "2026-09-01", "country": "France"}
    assert timing.annotate_deadline(rec, None, NOW) is True and rec["deadline"] == "2026-09-01"  # no text this run: nothing is proven
    rec = {"country": "France", "ai": {"deadline": "2026-10-09"}}
    assert timing.annotate_deadline(rec, long, NOW) is True and rec["deadline"] == "2026-10-09"
    rec = {"country": "France", "ai": {"deadline": "2031-01-01"}}
    assert timing.annotate_deadline(rec, long, NOW) is False  # an implausible AI date is ignored
