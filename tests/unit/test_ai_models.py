"""The model chain: fall back when a model cannot be used, stop on quota, read every response shape."""
from conftest import FakeResponse, FakeSession, make_settings

from geojobbot.ai.ask import answer, job_line
from geojobbot.ai.client import DEFAULT_MODELS, LEGACY_MODEL, WorkersAI, extract_text
from geojobbot.utils.text import job_code

ACCOUNT = "0123456789abcdef0123456789abcdef"
BASE = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/run/"


def ok(text):
    return FakeResponse(200, {"success": True, "result": {"response": text}})


def test_every_response_shape_is_read():
    assert extract_text({"response": " hello "}) == "hello"
    assert extract_text({"choices": [{"message": {"content": "from chat completions"}}]}) == "from chat completions"
    assert extract_text({"output": [{"type": "reasoning", "content": [{"type": "reasoning_text", "text": "thinking…"}]},
                                    {"type": "message", "content": [{"type": "output_text", "text": "the answer"}]}]}) == "the answer"
    assert extract_text({"response": {"fit": 7}}) == '{"fit": 7}'
    assert extract_text({"response": ""}) is None and extract_text(None) is None and extract_text({"output": []}) is None


def test_an_unusable_model_falls_back_and_the_worst_case_is_the_old_model():
    routes = {BASE + DEFAULT_MODELS[0]: FakeResponse(400, {"success": False, "errors": [{"code": 5007, "message": "No such model"}]}),
              BASE + DEFAULT_MODELS[1]: FakeResponse(200, {"success": True, "result": {"response": ""}}),  # answers nothing usable
              BASE + LEGACY_MODEL: ok('{"fit": 6}')}
    session = FakeSession(routes)
    ai = WorkersAI(ACCOUNT, "token", session=session, budget=5)
    assert ai.chat_json("s", "u") == {"fit": 6} and ai.last_model == LEGACY_MODEL and ai.failures == 0
    assert ai.chat("s", "u") == '{"fit": 6}' and len(session.calls) == 4  # the dead models are not tried again
    assert ai.used == {LEGACY_MODEL: 2}


def test_the_best_model_is_used_when_it_works_and_reasoning_models_get_room():
    session = FakeSession({BASE + DEFAULT_MODELS[0]: FakeResponse(200, {"success": True, "result": {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "fine"}]}]}})})
    ai = WorkersAI(ACCOUNT, "token", session=session)
    assert ai.chat("s", "u", max_tokens=300) == "fine" and ai.last_model == DEFAULT_MODELS[0]
    assert session.calls[0][2]["max_tokens"] == 1200  # thinking is output too


def test_quota_stops_ai_for_the_run_instead_of_walking_down_the_chain():
    session = FakeSession({BASE: FakeResponse(429, {"success": False, "errors": [{"message": "you have used up your daily free allocation of 10,000 neurons"}]})})
    ai = WorkersAI(ACCOUNT, "token", session=session, budget=9)
    assert ai.chat("s", "u") is None and ai.exhausted and ai.models == DEFAULT_MODELS
    assert ai.chat("s", "u") is None and len(session.calls) == 1


def test_settings_build_the_chain_and_an_explicit_model_goes_first():
    s = make_settings(cloudflare_ai_token="t", cloudflare_account_id=ACCOUNT)
    assert WorkersAI.from_settings(s).models == DEFAULT_MODELS
    s.ai_model = "@cf/meta/llama-4-scout-17b-16e-instruct"
    assert WorkersAI.from_settings(s).models[0] == "@cf/meta/llama-4-scout-17b-16e-instruct" and LEGACY_MODEL in WorkersAI.from_settings(s).models


def test_ask_answers_from_the_matches_only_and_flags_invented_codes():
    rec = {"canonical_id": "gh:acme:1", "title": "GIS Analyst | Senior", "company": "Acme", "city": "Lyon", "country": "France", "score": 82,
           "tier": "high", "salary": "EUR 52000 yearly", "visa": {"path": "Titre de séjour salarié", "verdict": "open"}, "deadline": "2026-10-01",
           "matched_skills": ["QGIS (required)", "PostGIS"], "ai": {"fit": 8, "summary": "Municipal GIS role", "languages": ["French"]}}
    code = job_code("gh:acme:1")
    line = job_line(rec)
    assert line.startswith(f"{code} | 82 | high | GIS Analyst Senior | Acme | Lyon, France") and "closes 2026-10-01" in line and "visa route" in line
    session = FakeSession({BASE + LEGACY_MODEL: [ok(f"The Lyon role [{code}] needs French and closes on 1 October. <b>bold</b>"),
                                                 ok("Try [abcde] instead.")]})
    ai = WorkersAI(ACCOUNT, "token", model=LEGACY_MODEL, session=session)
    text = answer(ai, "", "which need French?", [rec], "2026-09-20")
    assert f"<code>{code}</code>" in text and "&lt;b&gt;bold" in text  # codes become handles, model markup is escaped
    assert code in session.calls[0][2]["messages"][0]["content"] and "which need French?" == session.calls[0][2]["messages"][1]["content"]
    assert "cites codes I do not have (abcde)" in answer(ai, "", "anything else?", [rec], "2026-09-20")
    assert answer(ai, "", "q", [], "2026-09-20") is None
