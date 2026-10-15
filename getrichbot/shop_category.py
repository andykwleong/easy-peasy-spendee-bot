"""Match an emailed shop to a category that already exists.

The sheet category list is the only allowed set. Nothing here invents a name.
A shop is looked up only after the keyword matcher and the category name both
miss, and only the shop name is looked up. The model then picks one of the
existing names, or nothing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from getrichbot.categories import ALL_CATEGORIES
from getrichbot.categories import CATEGORY_KEYWORDS
from getrichbot.categories import forget_shop_keyword
from getrichbot.categories import remember_priority_keyword
from getrichbot.parser import categorize_description

LOGGER = logging.getLogger(__name__)

_PREFIX_RE = re.compile(r"^(?:[A-Za-z]{1,12}\s*\*\s*)+")
_GRAB_SHOP_RE = re.compile(r"\bgpay\b|\bgrab(?:food)?\b", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_MAILBOX_LINE_RE = re.compile(
    r"[^<>@]+<\s*[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\s*>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShopCategory:
    category: str | None
    learned_keyword: str = ""
    lookup_query: str = ""


@dataclass(frozen=True)
class PurchaseCategory:
    category: str = ""
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShopTap:
    status: str
    category: str = ""
    entry_id: str = ""


SHOP_CATEGORY_CALLBACK = "shopcat"


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


def is_email_shop(value: str) -> bool:
    """True when the text is an email address, not a store name."""
    text = " ".join((value or "").split())
    if not text or "@" not in text:
        return False
    bare = text.strip("<>").strip()
    if _EMAIL_RE.fullmatch(bare):
        return True
    return _MAILBOX_LINE_RE.fullmatch(text) is not None


def category_from_keywords(shop: str, logged_by: str, me_label: str, wife_label: str) -> str | None:
    category, confidence = categorize_description(shop, logged_by, me_label, wife_label)
    if category is None or confidence < 0.7:
        return None
    if category.lower().startswith("income"):
        return None
    return category


def category_for_purchase(
    body,
    shop,
    logged_by,
    categories,
    category_for,
    remember_category=None,
    expense_records=None,
    stop_keyword=None,
) -> str:
    """Category for a card purchase. Mail kind is already purchase."""
    return purchase_category(
        body,
        shop,
        logged_by,
        categories,
        category_for,
        remember_category,
        expense_records,
        stop_keyword,
    ).category


def purchase_category(
    body,
    shop,
    logged_by,
    categories,
    category_for,
    remember_category=None,
    expense_records=None,
    stop_keyword=None,
) -> PurchaseCategory:
    """Category for a card purchase, including a mixed shop that must be asked.

    Past confirmed expense rows for the same shop come first. One category is
    reused. More than one category is not guessed and is not looked up. A shop
    with no past rows still uses the keyword, the category name, and the lookup.
    Grab and GPAY use the transport category only on that no-history path.
    """
    history = _shop_history(expense_records, shop)
    if len(history) == 1:
        return PurchaseCategory(history[0])
    if len(history) > 1:
        _stop_shop_keyword(stop_keyword, shop)
        return PurchaseCategory("", history)

    sheet = tuple(category for category in categories if category)
    if is_grab_shop(shop):
        transport = existing_transport_category(sheet) or ""
        if transport:
            _remember_purchase_shop(remember_category, shop, transport)
        return PurchaseCategory(transport)
    expense_names = tuple(category for category in sheet if not category.casefold().startswith("income -"))
    named = category_named_in_text(body, expense_names)
    if named:
        _remember_purchase_shop(remember_category, shop, named)
        return PurchaseCategory(named)
    try:
        try:
            category = category_for(shop, logged_by, body) or ""
        except TypeError:
            category = category_for(shop, logged_by) or ""
    except Exception:
        LOGGER.exception("Could not choose a category. The purchase is still logged with a blank category.")
        category = ""
    if str(category).casefold().startswith("income -"):
        return PurchaseCategory("")
    if category:
        _remember_purchase_shop(remember_category, shop, category)
    return PurchaseCategory(str(category or ""))


def confirmed_shop_categories(records, shop: str) -> tuple[str, ...]:
    """Distinct expense categories already confirmed for this shop, first seen first."""
    return _categories_for_shop(records, shop)


def shop_is_mixed_after(records, record, category: str) -> bool:
    """True when this correction leaves the shop with more than one category."""
    categories = _categories_for_shop(records, getattr(record, "description", ""), edited=record, new_category=category)
    return len(categories) > 1


def shop_choice_state_key(entry_id: str) -> str:
    cleaned = "".join(character for character in (entry_id or "") if character.isalnum())
    return f"shop_category_choices:{cleaned}"


def shop_choices_from_state(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    choices = []
    for item in data:
        text = " ".join(str(item).split())
        if text and text not in choices:
            choices.append(text)
    return tuple(choices)


def shop_category_callback(entry_id: str, index: int) -> str:
    return f"{SHOP_CATEGORY_CALLBACK}|{entry_id}|{index}"


def resolve_shop_category_tap(entry_id: str, index: int, choices: tuple[str, ...], record, actor: str | None) -> ShopTap:
    """Only the person who submitted the mail may choose, and only a listed category."""
    if record is None or not entry_id:
        return ShopTap("missing")
    logged_by = str(getattr(record, "logged_by", "") or "")
    if not actor or actor != logged_by:
        return ShopTap("wrong_person", entry_id=entry_id)
    if index < 0 or index >= len(choices):
        return ShopTap("invalid", entry_id=entry_id)
    return ShopTap("ok", category=choices[index], entry_id=str(getattr(record, "entry_id", "") or entry_id))


def record_shop_category_choice(sheets, sheet_name: str, record, category: str) -> None:
    """Update this expense row only. The shop is not saved as a keyword."""
    sheets.update_expense_record(
        sheet_name,
        record.row_number,
        category=category,
        transaction_type="Expense",
    )


def _remember_purchase_shop(remember_category, shop: str, category: str) -> None:
    if remember_category is None or not shop or not category:
        return
    keyword = shop_keyword(shop)
    if not keyword:
        return
    try:
        remember_category(keyword, category)
    except Exception:
        LOGGER.exception("Could not save the shop keyword. The purchase is still logged.")


def is_grab_shop(shop: str) -> bool:
    """GPAY and Grab are the same transport merchant."""
    return _GRAB_SHOP_RE.search(shop or "") is not None


def existing_transport_category(categories: tuple[str, ...]) -> str | None:
    """A category already on the list whose name or keyword is transport.

    Nothing here invents the name. An income category is not transport.
    """
    names = tuple(
        category
        for category in categories
        if category and not category.casefold().startswith("income")
    )
    for category in names:
        folded = category.casefold()
        if folded == "transport" or folded.startswith("transport ") or folded.startswith("transport-"):
            return category
    for category in names:
        for keyword in CATEGORY_KEYWORDS.get(category, ()):
            if str(keyword).casefold() == "transport":
                return category
    return None


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
    if is_grab_shop(shop):
        transport = existing_transport_category(sheet_categories)
        if transport:
            return ShopCategory(transport, shop_keyword(shop))
        return ShopCategory(None)
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


def stop_shop_keyword(sheets, sheet_name: str, shop: str) -> None:
    """Turn off a shop keyword so the next charge is not pinned to one category."""
    keyword = shop_keyword(shop)
    if not keyword:
        return
    try:
        sheets.deactivate_category_keyword(sheet_name, keyword)
    except Exception:
        LOGGER.exception("Could not turn off the shop keyword. This charge will still ask.")
    forget_shop_keyword(keyword)


def apply_email_shop_correction(sheets, sheet_name: str, record, category: str | None, records=None) -> None:
    if not category:
        return
    keyword = shop_keyword(getattr(record, "description", ""))
    if (
        keyword
        and str(getattr(record, "input_type", "")).casefold() == "email"
        and records is not None
        and shop_is_mixed_after(records, record, category)
    ):
        try:
            stop_shop_keyword(sheets, sheet_name, keyword)
        except Exception:
            LOGGER.exception("Could not stop the shop keyword. The row change is already saved.")
        return

    def save(saved_keyword: str, new_category: str) -> None:
        apply_shop_keyword(sheets, sheet_name, saved_keyword, new_category)

    try:
        remember_email_shop_correction(record, category, save)
    except Exception:
        LOGGER.exception("Could not update the shop keyword. The row change is already saved.")


def _shop_history(expense_records, shop: str) -> tuple[str, ...]:
    if expense_records is None or not shop_keyword(shop):
        return ()
    try:
        records = expense_records() if callable(expense_records) else expense_records
    except Exception:
        LOGGER.exception("Could not read past expenses for this shop. The usual category check still runs.")
        return ()
    return confirmed_shop_categories(records, shop)


def _stop_shop_keyword(stop_keyword, shop: str) -> None:
    if stop_keyword is None or not shop_keyword(shop):
        return
    try:
        stop_keyword(shop)
    except Exception:
        LOGGER.exception("Could not stop the shop keyword. This charge will still ask.")


def _categories_for_shop(records, shop: str, *, edited=None, new_category: str | None = None) -> tuple[str, ...]:
    keyword = shop_keyword(shop)
    if not keyword:
        return ()
    found: list[str] = []
    seen = False
    for record in records or ():
        same = edited is not None and _same_row(record, edited)
        if same:
            seen = True
        if not _confirmed_expense(record):
            continue
        if shop_keyword(getattr(record, "description", "")) != keyword:
            continue
        category = new_category if same and new_category is not None else getattr(record, "category", "")
        _add_category(found, category)
    if edited is not None and not seen and new_category is not None:
        if _confirmed_expense(edited) and shop_keyword(getattr(edited, "description", "")) == keyword:
            _add_category(found, new_category)
    return tuple(found)


def _confirmed_expense(record) -> bool:
    if str(getattr(record, "status", "")).casefold() != "confirmed":
        return False
    kind = str(getattr(record, "transaction_type", "") or "Expense").casefold()
    if kind in {"income", "fixed"}:
        return False
    if str(getattr(record, "input_type", "")).casefold() == "fixed":
        return False
    return True


def _add_category(found: list[str], category) -> None:
    text = " ".join(str(category or "").split())
    if not text or text.casefold().startswith("income"):
        return
    if text not in found:
        found.append(text)


def _same_row(left, right) -> bool:
    left_id = str(getattr(left, "entry_id", "") or "")
    right_id = str(getattr(right, "entry_id", "") or "")
    if left_id and right_id:
        return left_id.casefold() == right_id.casefold()
    left_row = getattr(left, "row_number", None)
    return left_row is not None and left_row == getattr(right, "row_number", None)


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
