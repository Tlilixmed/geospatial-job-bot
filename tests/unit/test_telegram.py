import re
from datetime import datetime, timezone

import pytest
import requests
from conftest import FakeResponse, FakeSession

from geojobbot.notifications.telegram import MAX_MESSAGE, TelegramNotifier, digest_keyboard, format_digest, format_job_message

STAMP = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)

TOKEN = "123456:SECRET-TOKEN-VALUE"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def rec(**kw):
    base = {"tier": "high", "score": 86, "title": "GIS Analyst <Senior>", "company": "Acme & Co", "remote": True,
            "remote_scope": None, "work_mode": "remote", "employment_type": "Full-time", "location_raw": "Remote",
            "matched_skills": ["ArcGIS Pro (required)", "Python"], "matched_domains": ["LiDAR"],
            "why_matched": ["GIS Analyst title", "ArcGIS Pro required"], "apply_url": "https://acme.com/j?a=1&b=2",
            "sources": [{"source_name": "greenhouse"}], "posted_at": "2026-09-15T00:00:00Z", "posted_at_reliable": True}
    base.update(kw)
    return base


def test_format_escapes_and_contains_sections():
    text = format_job_message(rec())
    assert "🔥 <b>HIGH MATCH — 86/100</b>" in text
    assert "GIS Analyst &lt;Senior&gt;" in text and "Acme &amp; Co" in text
    assert "📍 Remote" in text and "Worldwide" not in text
    assert "ArcGIS Pro, Python, LiDAR" in text and "• ArcGIS Pro required" in text
    assert 'href="https://acme.com/j?a=1&amp;b=2"' in text
    assert format_job_message(rec(tier="possible")).startswith("🟡")


def test_missing_fields_do_not_crash():
    text = format_job_message({"title": "X", "tier": "possible"})
    assert "Location not specified" in text


def test_success_requires_ok_true():
    s = FakeSession({URL: FakeResponse(200, {"ok": True, "result": {}})})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi") == (True, None)
    s = FakeSession({URL: FakeResponse(200, {"ok": False, "description": "weird"})})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi")
    assert not ok


def test_429_retry_after_then_success():
    sleeps = []
    s = FakeSession({URL: [FakeResponse(429, {"ok": False, "parameters": {"retry_after": 3}}), FakeResponse(200, {"ok": True})]})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=sleeps.append).send("hi")[0]
    assert 3 in sleeps


def test_error_redacts_token_and_network_error():
    s = FakeSession({URL: FakeResponse(400, {"ok": False, "description": f"bad chat for bot{TOKEN}"})})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None).send("hi")
    assert not ok and TOKEN not in err
    s = FakeSession({URL: [requests.exceptions.ConnectionError()]})
    ok, err = TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, max_retries=1).send("hi")
    assert not ok and "network" in err and TOKEN not in err


# ------------------------------------------------------------------ digest
def test_digest_groups_by_tier_and_numbers_in_order():
    recs = [rec(title="A"), rec(title="B", tier="possible", score=60), rec(title="C", score=80)]
    parts = format_digest(recs, now=STAMP)
    assert len(parts) == 1
    text, covered = parts[0]
    assert covered == [recs[0], recs[2], recs[1]]  # High matches first, in input order, then Possible
    assert "3 new matches" in text and "2 high · 1 possible" in text and "16 Sep 2026 18:00 UTC" in text
    order = [text.index(x) for x in ("🔥 <b>High matches</b>", "1. <b>A</b>", "2. <b>C</b>",
                                     "🟡 <b>Possible matches</b>", "3. <b>B</b>")]
    assert order == sorted(order)
    assert "Acme &amp; Co" in text and 'href="https://acme.com/j?a=1&amp;b=2"' in text
    assert "📍 Remote" in text and "📅 15 Sep" in text and "ArcGIS Pro, Python" in text
    assert "(part" not in text


def test_digest_splits_within_telegram_limit_and_tracks_records():
    recs = [rec(title=f"GIS Analyst {i} " + "x" * 160, score=99 - i % 30) for i in range(40)]
    parts = format_digest(recs, now=STAMP)
    assert len(parts) > 1 and all(len(text) <= MAX_MESSAGE for text, _ in parts)
    assert [r for _, batch in parts for r in batch] == recs
    assert f"(part 1/{len(parts)})" in parts[0][0] and "(cont.)" in parts[1][0]
    numbers = re.findall(r"^(\d+)\. <b>", "\n".join(text for text, _ in parts), flags=re.M)
    assert numbers == [str(i) for i in range(1, 41)]


def test_digest_update_marker_and_empty_input():
    assert format_digest([], now=STAMP) == []
    text, _ = format_digest([rec(notified=True, pending_update_alert=True)], now=STAMP)[0]
    assert "🔁 1. <b>" in text


def test_digest_collapses_the_same_posting_and_covers_every_record():
    recs = [rec(title="GIS Data Analyst", company="Pomerleau", canonical_id="p:1", city="Montreal", remote=False, location_raw="Montreal"),
            rec(title="GIS Data Analyst", company="Pomerleau Inc.", canonical_id="p:2", city="Quebec", remote=False, location_raw="Quebec"),
            rec(title="LiDAR Technician", company="ScanCo", canonical_id="s:1")]
    (text, covered), = format_digest(recs, now=STAMP)
    assert covered == recs and text.count("GIS Data Analyst") == 1 and "×2" in text
    assert "Montreal | Quebec" in text and "2. <b>LiDAR Technician</b>" in text and "3. <b>" not in text
    assert "3 new matches" in text  # the header still counts jobs, not lines


def test_long_replies_are_split_between_blocks_and_bad_markup_is_sent_plain():
    from geojobbot.notifications.telegram import split_message

    blocks = [f'{i}. <a href="https://x.example/{i}">GIS Analyst {i}</a> — Firm {i}\n   ' + "detail " * 40 for i in range(40)]
    parts = split_message("\n\n".join(blocks))
    assert len(parts) > 1 and all(len(p) <= MAX_MESSAGE for p in parts)
    assert all(p.count("<a ") == p.count("</a>") for p in parts)  # never cut through a tag
    assert "\n\n".join(parts) == "\n\n".join(blocks)  # nothing lost
    s = FakeSession({URL: [FakeResponse(200, {"ok": True})] * len(parts)})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, delay_s=0).send("\n\n".join(blocks)) == (True, None)
    assert len(s.calls) == len(parts)
    # Telegram refuses the markup: the same text goes out again without it, instead of being lost
    s = FakeSession({URL: [FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities: unsupported start tag"}),
                           FakeResponse(200, {"ok": True})]})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, delay_s=0).send("<b>Esri</b> pays <50k &amp; more")[0]
    plain = s.calls[1][2]
    assert "parse_mode" not in plain and plain["text"] == "Esri pays <50k & more"


# ------------------------------------------------------------------ digest buttons (REVIEW O1)
CALLBACK_RE = re.compile(r"^([mahurwx])\|((?:[0-9a-f]{5}){0,3})$")  # the Worker's CALLBACK_RE


def button(number, *codes):
    return {"text": str(number), "callback_data": "m|" + "".join(codes)}


@pytest.mark.parametrize("numbered, rows", [
    ([], None),
    ([(1, [])], None),  # a record without an id has no code, hence no button
    ([(1, ["75288"])], [[button(1, "75288")]]),
    ([(4, ["75288", "450f5", "a0193"]), (5, []), (6, ["a9a87"])], [[button(4, "75288", "450f5", "a0193"), button(6, "a9a87")]]),
    ([(n, ["75288"]) for n in range(1, 10)], [[button(n, "75288") for n in range(1, 9)], [button(9, "75288")]]),
    ([(n, ["75288"]) for n in range(1, 91)], [[button(n, "75288") for n in range(r, min(r + 8, 91))] for r in range(1, 91, 8)]),
    ([(n, ["75288"]) for n in range(1, 92)], None),  # 91 buttons: over the cap, so no keyboard at all
])
def test_digest_keyboard_golden(numbered, rows):
    markup = digest_keyboard(numbered)
    assert markup == (None if rows is None else {"inline_keyboard": rows})
    for row in (markup or {}).get("inline_keyboard", []):
        assert len(row) <= 8
        for b in row:
            assert CALLBACK_RE.match(b["callback_data"]) and len(b["callback_data"].encode()) <= 64


def test_digest_with_keyboard_numbers_each_part_and_groups_codes():
    same = [rec(title="GIS Data Analyst", company="Pomerleau", canonical_id=f"p:{i}") for i in range(1, 5)]
    parts = format_digest(same + [rec(title="LiDAR Technician", company="ScanCo", canonical_id="s:1")], now=STAMP, with_keyboard=True)
    (text, covered, markup), = parts
    assert len(covered) == 5 and "×4" in text
    assert markup == {"inline_keyboard": [[button(1, "75288", "450f5", "a0193"), button(2, "a9a87")]]}  # three codes at most
    many = [rec(title=f"GIS Analyst {i} " + "x" * 160, canonical_id=f"x:{i}") for i in range(40)]
    parts = format_digest(many, now=STAMP, with_keyboard=True)
    assert len(parts) > 1
    for text, _, markup in parts:  # each part's buttons are the numbers that part shows
        shown = re.findall(r"^(\d+)\. <b>", text, flags=re.M)
        assert [b["text"] for row in markup["inline_keyboard"] for b in row] == shown
    assert [len(p) for p in format_digest(many, now=STAMP)] == [2] * len(parts)  # without it: the usual pairs


def test_send_puts_buttons_on_the_last_part_and_rings_only_once():
    blocks = [f"{i}. <b>GIS Analyst {i}</b>\n   " + "detail " * 40 for i in range(40)]
    text = "\n\n".join(blocks)
    markup = {"inline_keyboard": [[button(1, "75288")]]}
    s = FakeSession({URL: FakeResponse(200, {"ok": True})})
    assert TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, delay_s=0).send(text, reply_markup=markup)[0]
    payloads = [call[2] for call in s.calls]
    assert len(payloads) > 1
    assert [p.get("reply_markup") for p in payloads] == [None] * (len(payloads) - 1) + [markup]
    assert [p.get("disable_notification", False) for p in payloads] == [False] + [True] * (len(payloads) - 1)
    assert all(p["link_preview_options"] == {"is_disabled": True} and "disable_web_page_preview" not in p for p in payloads)
    s = FakeSession({URL: FakeResponse(200, {"ok": True})})
    TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, delay_s=0).send("weekly", silent=True)
    assert s.calls[0][2]["disable_notification"] is True and "reply_markup" not in s.calls[0][2]
    # the plain-text fallback keeps the buttons and the loudness of the original
    s = FakeSession({URL: [FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities"}), FakeResponse(200, {"ok": True})]})
    TelegramNotifier(TOKEN, "1", session=s, sleep=lambda x: None, delay_s=0).send("<b>x", reply_markup=markup, silent=True)
    assert s.calls[1][2]["reply_markup"] == markup and s.calls[1][2]["disable_notification"] is True


def test_command_menu_is_published_per_chat_and_language_and_the_default_list_removed():
    from geojobbot.notifications.commands import HELP
    from geojobbot.notifications.telegram import BOT_COMMANDS, publish_commands

    api = f"https://api.telegram.org/bot{TOKEN}/"
    assert len(BOT_COMMANDS) == 12
    for command, *descriptions in BOT_COMMANDS:
        assert re.fullmatch(r"[a-z0-9_]{1,32}", command) and (f"/{command}" in HELP or command == "help")  # one the bot answers
        assert all(1 <= len(d) <= 256 for d in descriptions)
    s = FakeSession({api + "setMyCommands": FakeResponse(200, {"ok": True, "result": True}),
                     api + "getMyCommands": FakeResponse(200, {"ok": True, "result": [{"command": "start", "description": "x"}]}),
                     api + "deleteMyCommands": FakeResponse(200, {"ok": True, "result": True})})
    steps = publish_commands(TOKEN, "-1001234", "4242", session=s)
    sets = [call[2] for call in s.calls if call[1].endswith("/setMyCommands")]
    assert [(p["scope"], p.get("language_code")) for p in sets] == [
        ({"type": "chat", "chat_id": chat}, language) for chat in ("-1001234", "4242") for language in (None, "fr", "ar")]
    assert [c["command"] for c in sets[1]["commands"]] == [c[0] for c in BOT_COMMANDS]
    assert sets[2]["commands"][0]["description"] == "أفضل الوظائف المناسبة حاليًا"
    assert s.calls[-1][1].endswith("/deleteMyCommands") and all(ok for _, ok, _ in steps)
    assert not any("1234" in name or "4242" in name for name, _, _ in steps)  # chat ids are not printed
    # a private chat: one scope; nothing left over: nothing deleted; a refusal is reported without the token
    s = FakeSession({api + "setMyCommands": FakeResponse(400, {"ok": False, "description": f"Bad Request: chat not found bot{TOKEN}"}),
                     api + "getMyCommands": FakeResponse(200, {"ok": True, "result": []})})
    steps = publish_commands(TOKEN, "4242", "4242", session=s)
    assert len(steps) == 3 and not any(ok for _, ok, _ in steps)
    assert all(TOKEN not in detail and "chat not found" in detail for _, _, detail in steps)
    assert not any(call[1].endswith("/deleteMyCommands") for call in s.calls)
