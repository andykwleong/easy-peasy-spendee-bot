"""Match an emailed shop to a category that already exists.

The sheet category list is the only allowed set. Nothing here invents a name.
A shop is looked up only after the keyword matcher and the category name both
miss, and only the shop name is looked up. The model then picks one of the
existing names, or nothing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from getrichbot.categories import ALL_CATEGORIES
from getrichbot.categories import remember_priority_keyword
from getrichbot.email_mail import category_from_keywords, is_email_shop

LOGGER = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{1,12}\s*\*\s*)+")


@dataclass(frozen=True)
class ShopCategory:
    category: str | None
    learned_keyword: str = ""
    lookup_query: str = ""


def strip_payment_prefix(shop: str) -> str:
    text = " ".join((shop or "").split())
    text = _PREFIX_RE.sub("", text).strip(" -:.")
    return " ".join(text.split())


def shop_keyword(shop: str) -> str:
    keyword = strip_payment_prefix(shop).casefold()
    if is_email_shop(shop) or is_email_shop(keyword):
        return ""
    return keyword


def category_named_in_text(text: str, categories: tuple[str, ...]) -> str | None:
    """Longest unique sheet category written in the text, including income names."""
    return _category_name_in_text(text, tuple(category for category in categories if category))


def resolve_shop_category(
    shop: str,
    logged_by: str,
    me_label: str,
    wife_label: str,
    categories: tuple[str, ...],
    lookup,
    ask=None,
) -> ShopCategory:
    if is_email_shop(shop):
        return ShopCategory(None)
    sheet_categories = tuple(category for category in categories if category)
    allowed = tuple(category for category in sheet_categories if not category.casefold().startswith("income"))
    known = category_from_keywords(shop, logged_by, me_label, wife_label)
    if known in allowed:
        return ShopCategory(known)
    named = _category_name_in_text(shop, allowed)
    if named is not None:
        return ShopCategory(named)

    query = strip_payment_prefix(shop)
    if not query or not any(character.isalpha() for character in query):
        return ShopCategory(None)

    try:
        looked_up = lookup(query)
    except Exception:
        LOGGER.exception("Shop lookup failed. The purchase is still logged with a blank category.")
        looked_up = None

    description = str(looked_up).strip() if looked_up else ""
    if not description or ask is None:
        return ShopCategory(None, "", query)

    try:
        chosen = ask(query, description, sheet_categories)
    except Exception:
        LOGGER.exception("Shop category model failed. The purchase is still logged with a blank category.")
        chosen = None
    accepted = _accepted_category(chosen, sheet_categories)
    if accepted is None:
        return ShopCategory(None, "", query)
    return ShopCategory(accepted, shop_keyword(query), query)


def categorize_email_shop(
    shop: str,
    logged_by: str,
    me_label: str,
    wife_label: str,
    categories: tuple[str, ...],
    lookup,
    save,
    ask=None,
) -> str | None:
    result = resolve_shop_category(shop, logged_by, me_label, wife_label, categories, lookup, ask)
    if result.category and result.learned_keyword:
        try:
            save(result.learned_keyword, result.category)
        except Exception:
            LOGGER.exception("Could not save the shop keyword. The purchase is still logged.")
    return result.category


def remember_email_shop_correction(record, category: str, save) -> None:
    if str(getattr(record, "input_type", "")).casefold() != "email":
        return
    if not category or category not in ALL_CATEGORIES:
        return
    keyword = shop_keyword(getattr(record, "description", ""))
    if not keyword:
        return
    save(keyword, category)


def apply_shop_keyword(sheets, sheet_name: str, keyword: str, category: str) -> None:
    sheets.upsert_category_keyword(sheet_name, keyword, category)
    remember_priority_keyword(keyword, category)


def apply_email_shop_correction(sheets, sheet_name: str, record, category: str | None) -> None:
    if not category:
        return

    def save(keyword: str, new_category: str) -> None:
        apply_shop_keyword(sheets, sheet_name, keyword, new_category)

    try:
        remember_email_shop_correction(record, category, save)
    except Exception:
        LOGGER.exception("Could not update the shop keyword. The row change is already saved.")


def _category_name_in_text(text: str, categories: tuple[str, ...]) -> str | None:
    matches = [category for category in categories if _contains_phrase(text, category)]
    if not matches:
        return None
    matches.sort(key=len, reverse=True)
    if len(matches) > 1 and len(matches[0]) == len(matches[1]):
        return None
    return matches[0]


def _accepted_category(chosen, categories: tuple[str, ...]) -> str | None:
    if chosen is None:
        return None
    text = " ".join(str(chosen).split())
    if not text:
        return None
    for category in categories:
        if category == text or category.casefold() == text.casefold():
            return category
    return None


def _contains_phrase(text: str, phrase: str) -> bool:
    cleaned = " ".join(phrase.casefold().split())
    if not cleaned:
        return False
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(part) for part in cleaned.split()) + r"(?![a-z0-9])"
    return re.search(pattern, text.casefold()) is not None
