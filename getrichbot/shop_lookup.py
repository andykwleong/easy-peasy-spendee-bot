"""One shop-name lookup on Exa. Not a poll.

Only the shop name is sent. The amount, the card, the last 4 digits, and the
rest of the email stay here. There is no retry and no scheduled repeat.
A missing key or a failed search returns nothing. It does not raise.
"""

from __future__ import annotations

import json
import logging
import urllib.request

LOGGER = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"
_TIMEOUT_SECONDS = 8
_DESCRIPTION_LIMIT = 600


def exa_search_body(shop: str) -> dict:
    query = " ".join((shop or "").split())
    return {
        "query": query,
        "type": "auto",
        "numResults": 1,
        "contents": {"highlights": True},
    }


def lookup_shop_text(shop: str, api_key: str | None = None, *, fetch=None) -> str | None:
    query = " ".join((shop or "").split())
    if not query or not any(character.isalpha() for character in query):
        return None
    key = (api_key or "").strip()
    if not key:
        return None
    getter = fetch or _post_exa
    try:
        raw = getter(EXA_SEARCH_URL, exa_search_body(query), key)
    except Exception:
        LOGGER.exception("Exa shop lookup failed. The purchase is still logged with a blank category.")
        return None
    if not isinstance(raw, str):
        return None
    return exa_description(raw)


def exa_description(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return None
    first = results[0]
    parts: list[str] = []
    title = first.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(title.strip())
    highlights = first.get("highlights")
    if isinstance(highlights, list):
        for item in highlights:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
    text = " ".join(parts).strip()
    if not text:
        return None
    return text[:_DESCRIPTION_LIMIT]


def _post_exa(url: str, body: dict, api_key: str) -> str:
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")
