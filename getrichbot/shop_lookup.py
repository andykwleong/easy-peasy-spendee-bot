"""One shop-name lookup. Not a poll, and not OpenAI.

Only the shop name is sent. The amount, the card, the last 4 digits, and the
rest of the email stay here. There is no retry and no scheduled repeat.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

LOGGER = logging.getLogger(__name__)

_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "GetRichBot/1.0 (household expense categories; one shop-name lookup)"
_TIMEOUT_SECONDS = 5


def wikipedia_search_url(shop: str) -> str:
    query = " ".join((shop or "").split())
    return _WIKIPEDIA_API + "?" + urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": "1",
            "namespace": "0",
            "format": "json",
        }
    )


def lookup_shop_text(shop: str, fetch=None) -> str | None:
    query = " ".join((shop or "").split())
    if not query or not any(character.isalpha() for character in query):
        return None
    getter = fetch or _fetch_text
    try:
        raw = getter(wikipedia_search_url(query))
    except Exception:
        LOGGER.exception("Shop lookup failed. The purchase can still use a plain word match.")
        return None
    return opensearch_text(raw)


def opensearch_text(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list) or len(data) < 3:
        return None
    titles = data[1] if isinstance(data[1], list) else []
    descriptions = data[2] if isinstance(data[2], list) else []
    parts = [item.strip() for item in [*titles, *descriptions] if isinstance(item, str) and item.strip()]
    text = " ".join(parts).strip()
    return text or None


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")
