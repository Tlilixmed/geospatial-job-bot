"""Cloudflare Workers AI over the REST API (free daily allowance; no SDK needed).

POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}
Authorization: Bearer <token with "Workers AI: Read">

The account id is taken from CLOUDFLARE_ACCOUNT_ID or, failing that, from the R2 endpoint
(https://<account_id>.r2.cloudflarestorage.com), so only the token has to be added as a secret.
Every failure is soft: callers get None and carry on without AI.
"""
from __future__ import annotations

import json
import logging
import re

import requests

log = logging.getLogger(__name__)

DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct"


def account_id_from(settings) -> str | None:
    if getattr(settings, "cloudflare_account_id", None):
        return settings.cloudflare_account_id
    match = re.match(r"https://([0-9a-f]{32})\.(?:eu\.|fedramp\.)?r2\.cloudflarestorage\.com", settings.r2_endpoint_url or "")
    return match.group(1) if match else None


class WorkersAI:
    def __init__(self, account_id: str, token: str, *, model: str = DEFAULT_MODEL, session=None, budget: int = 25,
                 timeout: float = 60.0):
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self.token = token
        self.session = session or requests.Session()
        self.budget = budget          # calls this process may still make
        self.timeout = timeout
        self.calls = 0
        self.failures = 0

    @classmethod
    def from_settings(cls, settings, *, session=None, budget: int | None = None) -> "WorkersAI | None":
        account, token = account_id_from(settings), getattr(settings, "cloudflare_ai_token", None)
        if not (account and token):
            return None
        return cls(account, token, model=settings.ai_model or DEFAULT_MODEL, session=session,
                   budget=settings.ai_reviews_per_run if budget is None else budget)

    def chat(self, system: str, user: str, *, max_tokens: int = 300) -> str | None:
        """One completion, or None when out of budget or on any error (never raises)."""
        if self.budget <= 0 or self.failures >= 3:  # three strikes: stop spending time on a broken service
            return None
        self.budget -= 1
        self.calls += 1
        try:
            response = self.session.post(
                self.url, timeout=self.timeout, headers={"Authorization": f"Bearer {self.token}"},
                json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                      "max_tokens": max_tokens, "temperature": 0})
            body = response.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            self.failures += 1
            log.warning("workers ai: %s", type(exc).__name__)
            return None
        if response.status_code != 200 or not body.get("success", True):
            self.failures += 1
            log.warning("workers ai: HTTP %s %s", response.status_code, str(body.get("errors"))[:160])
            return None
        text = (body.get("result") or {}).get("response")
        return text.strip() if isinstance(text, str) and text.strip() else None

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
