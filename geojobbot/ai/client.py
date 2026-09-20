"""Cloudflare Workers AI over the REST API (free daily allowance of 10,000 neurons; no SDK needed).

POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Authorization: Bearer <token with "Workers AI: Read">

The account id is taken from CLOUDFLARE_ACCOUNT_ID or, failing that, from the R2 endpoint
(https://<account_id>.r2.cloudflarestorage.com), so only the token has to be added as a secret.
Every failure is soft: callers get None and carry on without AI.

Models. The allowance is counted in neurons, and the price list changes: in September 2026 the original
llama-3.1-8b costs 25,608 neurons per million input tokens, about the same as gpt-oss-120b (31,818) and three times
gemma-4-26b (9,091). So the client takes an ordered list (AI_MODELS), best first, and moves down the list when a model
cannot be used (unknown id, bad request, unreadable answer). The last entry is the model this bot has always used, so
the worst case is yesterday's behaviour. A quota or rate-limit answer stops AI for the run instead: the next model
would hit the same wall. Which model answered is recorded with every result.
"""
from __future__ import annotations

import json
import logging
import re

import requests

log = logging.getLogger(__name__)

LEGACY_MODEL = "@cf/meta/llama-3.1-8b-instruct"
DEFAULT_MODELS = ["@cf/openai/gpt-oss-120b", "@cf/google/gemma-4-26b-a4b-it", LEGACY_MODEL]
DEFAULT_MODEL = DEFAULT_MODELS[0]
REASONING_HINTS = ("gpt-oss", "qwq", "deepseek-r1", "qwen3", "glm-5", "kimi", "nemotron")  # they think before answering
REASONING_EXTRA_TOKENS = 900


def account_id_from(settings) -> str | None:
    if getattr(settings, "cloudflare_account_id", None):
        return settings.cloudflare_account_id
    match = re.match(r"https://([0-9a-f]{32})\.(?:eu\.|fedramp\.)?r2\.cloudflarestorage\.com", settings.r2_endpoint_url or "")
    return match.group(1) if match else None


def extract_text(result) -> str | None:
    """The answer text from any of the shapes Workers AI models return (classic, OpenAI chat, OpenAI responses)."""
    if isinstance(result, str):
        return result.strip() or None
    if not isinstance(result, dict):
        return None
    direct = result.get("response") if isinstance(result.get("response"), str) else result.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    if isinstance(result.get("response"), dict):  # some models nest the structured answer
        return json.dumps(result["response"])
    for choice in result.get("choices") or []:
        content = ((choice or {}).get("message") or {}).get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    texts = []
    for item in result.get("output") or []:
        if isinstance(item, dict) and item.get("type") in (None, "message"):
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and part.get("type") in (None, "output_text", "text"):
                    texts.append(part["text"])
    joined = "\n".join(t for t in texts if t.strip()).strip()
    return joined or None


class WorkersAI:
    def __init__(self, account_id: str, token: str, *, model: str | None = None, models: list[str] | None = None, session=None,
                 budget: int = 25, timeout: float = 90.0):
        chain = [m for m in (models or ([model] if model else DEFAULT_MODELS)) if m]
        self.models = list(dict.fromkeys(chain)) or [LEGACY_MODEL]
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"
        self.token = token
        self.session = session or requests.Session()
        self.budget = budget          # calls this process may still make
        self.timeout = timeout
        self.calls = 0
        self.failures = 0
        self.exhausted = False        # quota or rate limit: no model will answer during this run
        self.last_model: str | None = None
        self.used: dict[str, int] = {}

    @property
    def model(self) -> str:
        return self.models[0]

    @property
    def url(self) -> str:
        return self.base + self.model

    @classmethod
    def from_settings(cls, settings, *, session=None, budget: int | None = None) -> "WorkersAI | None":
        account, token = account_id_from(settings), getattr(settings, "cloudflare_ai_token", None)
        if not (account and token):
            return None
        models = list(getattr(settings, "ai_models", None) or [])
        if getattr(settings, "ai_model", None):  # an explicit single model (AI_MODEL) goes first
            models = [settings.ai_model] + [m for m in models if m != settings.ai_model]
        return cls(account, token, models=models or None, session=session,
                   budget=settings.ai_reviews_per_run if budget is None else budget)

    def _post(self, model: str, system: str, user: str, max_tokens: int):
        if any(hint in model for hint in REASONING_HINTS):
            max_tokens += REASONING_EXTRA_TOKENS  # the thinking is billed and counted as output: leave room for the answer
        return self.session.post(
            self.base + model, timeout=self.timeout, headers={"Authorization": f"Bearer {self.token}"},
            json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                  "max_tokens": max_tokens, "temperature": 0})

    def chat(self, system: str, user: str, *, max_tokens: int = 300) -> str | None:
        """One completion, or None when out of budget or on any error (never raises)."""
        if self.budget <= 0 or self.failures >= 3 or self.exhausted:  # three strikes: stop spending time on a broken service
            return None
        self.budget -= 1
        self.calls += 1
        while self.models:
            model = self.models[0]
            try:
                response = self._post(model, system, user, max_tokens)
            except requests.exceptions.RequestException as exc:
                self.failures += 1  # network trouble says nothing about the model: keep it
                log.warning("workers ai: %s", type(exc).__name__)
                return None
            try:
                body = response.json()
            except ValueError:
                body = {}  # an error page instead of JSON: judged by the status code below
            errors = str(body.get("errors"))[:200] if isinstance(body, dict) else ""
            if response.status_code == 429 or re.search(r"daily|quota|neurons|rate.?limit|capacity", errors, re.I):
                self.exhausted = True
                log.warning("workers ai: allowance or rate limit reached (HTTP %s); no more AI calls this run", response.status_code)
                return None
            text = extract_text(body.get("result")) if response.status_code == 200 and isinstance(body, dict) and body.get("success", True) else None
            if text:
                self.last_model = model
                self.used[model] = self.used.get(model, 0) + 1
                return text
            if len(self.models) > 1:  # this model cannot be used as called: fall back, and do not count it against the service
                log.warning("workers ai: %s unusable (HTTP %s %s); falling back to %s", model, response.status_code, errors, self.models[1])
                self.models.pop(0)
                continue
            self.failures += 1
            log.warning("workers ai: HTTP %s %s", response.status_code, errors)
            return None
        return None

    def chat_json(self, system: str, user: str, *, max_tokens: int = 350) -> dict | None:
        """Completion parsed as a JSON object (models wrap JSON in prose or code fences: take the outer braces)."""
        text = self.chat(system, user, max_tokens=max_tokens)
        if not text:
            return None
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", text[start:end + 1]))
            except ValueError:
                return None
        return data if isinstance(data, dict) else None
