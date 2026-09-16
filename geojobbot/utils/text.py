"""Text helpers: HTML to text, normalisation, hashing."""
from __future__ import annotations

import hashlib
import html as html_lib
import re
import unicodedata
import warnings

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n{3,}")
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|l\.l\.c|corp|corporation|co|company|gmbh|ag|sa|s\.a|sas|srl|bv|b\.v|nv|plc|pty|pte|oy|ab|group|holdings)\b\.?",
    re.IGNORECASE,
)
_TITLE_NOISE_RE = re.compile(
    r"\((?:m/w/d|f/m/d|m/f/d|w/m/d|h/f|f/h|m/f|all genders?)\)|\b(?:m/w/d|f/m/d|m/f/d|h/f)\b",
    re.IGNORECASE,
)


def html_to_text(value: str | None) -> str:
    """Convert HTML (possibly entity-escaped) to readable plain text."""
    if not value:
        return ""
    text = str(value)
    if "&lt;" in text and "<" not in text:
        text = html_lib.unescape(text)
    if "<" not in text:
        return clean_whitespace(html_lib.unescape(text))
    try:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for block in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"]):
            block.append("\n")
        out = soup.get_text()
    except Exception:  # extremely malformed markup: strip tags crudely
        out = re.sub(r"<[^>]+>", " ", text)
    return clean_whitespace(html_lib.unescape(out))


def clean_whitespace(text: str) -> str:
    lines = [_WS_RE.sub(" ", line).strip() for line in text.replace("\xa0", " ").split("\n")]
    joined = "\n".join(lines)
    return _NL_RE.sub("\n\n", joined).strip()


def fold(text: str | None) -> str:
    """Lowercase, strip accents and collapse whitespace."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", str(text))
    norm = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", norm).strip().lower()


def normalize_company(name: str | None) -> str:
    value = fold(name)
    value = _COMPANY_SUFFIX_RE.sub(" ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_title(title: str | None) -> str:
    value = _TITLE_NOISE_RE.sub(" ", title or "")
    value = fold(value)
    value = re.sub(r"[^a-z0-9/+#]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def sha1(value: str, length: int = 20) -> str:
    return hashlib.sha1(value.encode("utf-8", "replace")).hexdigest()[:length]


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def prettify_slug(slug: str) -> str:
    words = re.split(r"[-_.]+", slug or "")
    return " ".join(w.capitalize() for w in words if w) or (slug or "")
