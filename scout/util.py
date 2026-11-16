"""HTTP, dates and text helpers shared by every source."""
from __future__ import annotations

import html
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

log = logging.getLogger("scout")

# Career sites routinely reject non-browser agents. Nothing personal is ever sent.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"})
    return s


# Workday throttles per client IP across every tenant, so all Workday calls in the process
# share one queue with a minimum gap between them.
_WORKDAY_LOCK = threading.Lock()
_WORKDAY_GAP = 1.5
_workday_last = 0.0


def _pace(url: str) -> None:
    global _workday_last
    if "myworkdayjobs.com" not in url:
        return
    with _WORKDAY_LOCK:
        wait = _workday_last + _WORKDAY_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _workday_last = time.monotonic()


def _request(s: requests.Session, method: str, url: str, *, retries: int = 2, timeout: int = 30, **kw) -> Any:
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            _pace(url)
            r = s.request(method, url, timeout=timeout, **kw)
            # 4xx (other than throttling) will not get better on retry.
            if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                raise PermanentError(f"{method} {url} -> HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except PermanentError:
            raise
        except Exception as e:  # noqa: BLE001 - network, 5xx, 429, bad JSON: all retryable
            last = e
            if attempt < retries:
                # Throttled boards (Workday especially) need a real pause, not a quick retry.
                throttled = getattr(getattr(e, "response", None), "status_code", None) == 429
                time.sleep((8 if throttled else 1.5) * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed: {last}")


class PermanentError(RuntimeError):
    pass


def get_json(s, url: str, *, params: Optional[dict] = None, **kw) -> Any:
    return _request(s, "GET", url, params=params, **kw)


def post_json(s, url: str, body: dict, **kw) -> Any:
    return _request(s, "POST", url, json=body, headers={"Content-Type": "application/json"}, **kw)


# ---------------------------------------------------------------- dates

def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def iso_date(value: Any) -> Optional[str]:
    """ISO timestamp, epoch seconds or epoch milliseconds -> YYYY-MM-DD (UTC)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            n = float(value)
            if n > 1e11:
                n /= 1000
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OSError, OverflowError):
        m = re.match(r"\d{4}-\d{2}-\d{2}", str(value))
        return m.group(0) if m else None


def long_date(value: Optional[str]) -> Optional[str]:
    """'November 16, 2026' -> '2026-11-16'."""
    try:
        return datetime.strptime((value or "").strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def relative_date(text: Optional[str], today: Optional[date] = None) -> Optional[str]:
    """Workday's 'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' -> a date.

    'Posted 30+ Days Ago' is a floor, not a date; it maps to 31 days back, which is enough
    to keep the posting out of every freshness window.
    """
    if not text:
        return None
    today = today or today_utc()
    t = text.lower()
    if "today" in t or "just posted" in t:
        return today.isoformat()
    if "yesterday" in t:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\s*(\+)?\s*day", t)
    if m:
        return (today - timedelta(days=int(m.group(1)) + (1 if m.group(2) else 0))).isoformat()
    return None


def age_days(posted_at: Optional[str], today: date) -> Optional[int]:
    if not posted_at:
        return None
    try:
        return (today - date.fromisoformat(posted_at)).days
    except ValueError:
        return None


# ---------------------------------------------------------------- text

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</(p|div|li|h[1-6]|ul|ol|tr)>|<br\s*/?>", re.I)
_DROP = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)


def strip_html(text: Optional[str]) -> str:
    if not text:
        return ""
    text = html.unescape(text) if "&lt;" in text else text
    text = _DROP.sub(" ", text)
    text = _BLOCK.sub("\n", text)
    text = html.unescape(_TAG.sub(" ", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def norm_key(text: str) -> str:
    """Loose identity for company names and titles: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
