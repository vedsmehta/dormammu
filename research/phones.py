"""Phone numbers: find them, normalize them to E.164, and prove they appear in a source we fetched.

The model is told to copy phone numbers exactly as written in a source. We do not trust that: a number is
kept only when the same ten digits appear in the text of a cited page fetched here with httpx (10 second
timeout, text content only). Everything else becomes phone null, phone_verified false.

Unicode matters: models and web pages write "(650) 329‑2100" with a non-breaking hyphen, and pages hide
numbers in `href="tel:+16503292100"`. Both are matched. Only US numbers (NANP, +1) are accepted.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

FETCH_TIMEOUT_SECONDS = 10.0
MAX_FETCH_BYTES = 600_000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 DormammuResearch/0.1"
)
TEXT_TYPES = ("text/", "application/xhtml", "application/xml", "application/json", "application/ld+json")

# Separators that appear between phone groups: spaces, dots, ASCII and Unicode dashes, parentheses.
_SEP = r"[\s.\-()‐-―−­]*"
US_PHONE = re.compile(
    r"(?<![\d\w])(?:\+?\s?1" + _SEP + r")?\(?([2-9]\d{2})\)?" + _SEP + r"([2-9]\d{2})" + _SEP + r"(\d{4})(?!\d)"
)
TEL_HREF = re.compile(r"tel:\s*\+?\s*1?[\s\-.()]*(\d{3})[\s\-.()]*(\d{3})[\s\-.()]*(\d{4})", re.IGNORECASE)
_TAGS = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def normalize(raw: str | None) -> str | None:
    """E.164 for a US number written any common way, else None. Never raises."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] in "01" or digits[3] in "01":
        return None
    return "+1" + digits


def find_phones(text: str | None) -> list[str]:
    """Every distinct US phone number in `text`, as E.164, in order of first appearance."""
    out: list[str] = []
    if not text:
        return out
    for pattern in (TEL_HREF, US_PHONE):
        for m in pattern.finditer(text):
            e164 = normalize("".join(m.groups()))
            if e164 and e164 not in out:
                out.append(e164)
    return out


def mask(e164: str | None) -> str:
    """'+1415***0100': country and area code, then the last four. For logs, tables and the UI."""
    if not e164:
        return "(no phone)"
    digits = re.sub(r"\D", "", e164)
    if len(digits) < 7:
        return "***"
    return f"+{digits[:-7]}***{digits[-4:]}"


def digits_present(e164: str | None, text: str | None) -> bool:
    """True when the same ten digits appear in `text` as a phone number (any formatting, or a tel: link)."""
    if not e164 or not text:
        return False
    target = normalize(e164)
    return target is not None and target in find_phones(text)


def visible_text(raw: str | None) -> str:
    """Page text with scripts, styles and tags removed. Raw HTML is still searched for tel: links."""
    if not raw:
        return ""
    text = _TAGS.sub(" ", raw)
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", html.unescape(text)).strip()


def clean_url(url: str | None) -> str | None:
    """Drop tracking params (utm_*) and fragments; keep the rest as written. None for non-http URLs."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def url_key(url: str | None) -> str | None:
    """A matching key: lower-cased scheme and host, no www., no utm_*, no fragment, no trailing slash."""
    cleaned = clean_url(url)
    if not cleaned:
        return None
    parts = urlsplit(cleaned)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def host_of(url: str | None) -> str | None:
    cleaned = clean_url(url)
    if not cleaned:
        return None
    host = urlsplit(cleaned).netloc.lower()
    return host[4:] if host.startswith("www.") else host


async def fetch_text(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> str | None:
    """GET a page and return its raw text (HTML included) when it is a text type, else None. Never raises.

    Tests monkeypatch this function on the module; every caller reaches it as `phones.fetch_text`.
    """
    cleaned = clean_url(url)
    if not cleaned:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"}
        ) as client:
            response = await client.get(cleaned)
        if response.status_code >= 400:
            return None
        content_type = (response.headers.get("content-type") or "").lower()
        if content_type and not content_type.startswith(TEXT_TYPES):
            return None
        return response.text[:MAX_FETCH_BYTES]
    except Exception:  # noqa: BLE001
        return None


class PageCache:
    """One fetch per URL per research run, shared by phone and citation verification."""

    def __init__(self, timeout: float = FETCH_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self.pages: dict[str, str | None] = {}

    async def get(self, url: str | None) -> str | None:
        key = url_key(url)
        if key is None:
            return None
        if key not in self.pages:
            try:
                self.pages[key] = await fetch_text(url or "", self.timeout)
            except Exception:  # noqa: BLE001
                self.pages[key] = None
        return self.pages[key]

    def fetched_ok(self, url: str | None) -> bool:
        key = url_key(url)
        return key is not None and bool(self.pages.get(key))


async def verify_phone(e164: str | None, urls: list[str], cache: PageCache, max_pages: int = 3) -> str | None:
    """The first URL among `urls` whose fetched text contains `e164`, else None. Fetches at most max_pages."""
    target = normalize(e164)
    if not target:
        return None
    seen: set[str] = set()
    tried = 0
    for url in urls:
        key = url_key(url)
        if not key or key in seen:
            continue
        seen.add(key)
        if tried >= max_pages:
            break
        tried += 1
        text = await cache.get(url)
        if text and digits_present(target, text):
            return url
    return None
