from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_CATEGORY_CONFIG: dict[str, Any] = {
    "variable_categories": [
        "Food",
        "Groceries",
        "Utilities",
        "Insurance",
        "Childcare",
        "Shopping - Person A",
        "Shopping - Person B",
        "Transport",
        "Personal care",
        "Entertainment",
        "Gifts",
        "Income - A",
        "Income - FX",
        "Income - Misc",
    ],
    "fixed_categories": [
        "Rent or mortgage",
        "Loan repayment",
        "Subscriptions",
    ],
    "category_keywords": {
        "Food": ["food", "dinner", "lunch", "breakfast", "dessert", "snack", "coffee", "tea", "restaurant", "meal", "cafe"],
        "Groceries": ["grocery", "groceries", "supermarket", "market"],
        "Utilities": ["electricity", "electricity bill", "water bill", "utilities", "internet bill", "phone bill"],
        "Insurance": ["insurance"],
        "Childcare": ["baby", "childcare", "diaper", "diapers", "formula"],
        "Transport": ["taxi", "petrol", "parking", "car", "transport", "train", "bus"],
        "Personal care": ["haircut", "salon", "skincare", "facial", "personal care"],
        "Entertainment": ["movie", "cinema", "concert", "game", "games"],
        "Gifts": ["gift", "gifts", "present", "birthday"],
        "Income - A": ["income a", "salary a"],
        "Income - FX": ["income fx", "salary fx"],
        "Income - Misc": ["dividend", "dividends", "sale proceed", "sales proceeds", "interest", "bonus"],
    },
    "priority_keywords": [
        {"category": "Childcare", "keywords": ["baby", "childcare", "diaper", "diapers", "formula"]},
        {"category": "Utilities", "keywords": ["electricity", "electricity bill", "water bill", "utilities"]},
        {"category": "Insurance", "keywords": ["insurance"]},
    ],
    "shopping_keywords": ["shopping", "shop", "shopee", "lazada", "amazon", "clothes", "shirt", "dress", "shoes", "bag"],
    "shopping_categories": {
        "me": "Shopping - Person A",
        "wife": "Shopping - Person B",
    },
    "category_aliases": {
        "baby": "Childcare",
        "childcare": "Childcare",
        "grocery": "Groceries",
        "groceries": "Groceries",
        "electricity": "Utilities",
        "electricity bill": "Utilities",
        "utilities": "Utilities",
        "insurance": "Insurance",
        "gift": "Gifts",
        "gifts": "Gifts",
        "income a": "Income - A",
        "salary a": "Income - A",
        "income fx": "Income - FX",
        "salary fx": "Income - FX",
        "dividend": "Income - Misc",
        "dividends": "Income - Misc",
        "sale proceed": "Income - Misc",
        "sales proceeds": "Income - Misc",
        "interest": "Income - Misc",
        "bonus": "Income - Misc",
    },
}

CONFIG_SOURCE = "fallback"


def _load_category_config() -> dict[str, Any]:
    load_dotenv()

    raw_json = os.getenv("CATEGORIES_JSON")
    if raw_json:
        return json.loads(raw_json)

    file_raw = os.getenv("CATEGORIES_FILE")
    if file_raw:
        path = Path(file_raw).expanduser()
        return json.loads(path.read_text(encoding="utf-8"))

    local_categories = Path("categories.json")
    if local_categories.exists():
        return json.loads(local_categories.read_text(encoding="utf-8"))

    return DEFAULT_CATEGORY_CONFIG


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _keyword_map(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    return {str(category): _string_list(keywords) for category, keywords in raw.items()}


def _priority_keywords(raw: Any) -> list[tuple[str, list[str]]]:
    if not isinstance(raw, list):
        return []
    priorities: list[tuple[str, list[str]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", "")).strip()
        keywords = _string_list(item.get("keywords", []))
        if category and keywords:
            priorities.append((category, keywords))
    return priorities


def _string_map(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(key).strip().lower(): str(value).strip() for key, value in raw.items() if str(key).strip() and str(value).strip()}


def _validate_config(config: dict[str, Any]) -> None:
    if not VARIABLE_CATEGORIES:
        raise RuntimeError("Category config must include at least one variable category.")

    missing = [
        category
        for category in [*CATEGORY_KEYWORDS.keys(), *SHOPPING_CATEGORIES.values(), *CATEGORY_ALIASES.values()]
        if category and category not in ALL_CATEGORIES
    ]
    priority_missing = [category for category, _ in BILL_PRIORITY_KEYWORDS if category not in ALL_CATEGORIES]
    missing.extend(priority_missing)
    if missing:
        unique = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Category config references unknown categories: {unique}")


def configure_category_config(config: dict[str, Any]) -> None:
    global CONFIG_SOURCE
    variable_categories = _string_list(config.get("variable_categories", []))
    fixed_categories = _string_list(config.get("fixed_categories", []))
    all_categories = variable_categories + fixed_categories
    category_keywords = _keyword_map(config.get("category_keywords", {}))
    priority_keywords = _priority_keywords(config.get("priority_keywords", []))
    shopping_keywords = _string_list(config.get("shopping_keywords", []))
    shopping_categories = _string_map(config.get("shopping_categories", {}))
    category_aliases = _string_map(config.get("category_aliases", {}))

    if not variable_categories:
        raise RuntimeError("Category config must include at least one variable category.")

    missing = [
        category
        for category in [*category_keywords.keys(), *shopping_categories.values(), *category_aliases.values()]
        if category and category not in all_categories
    ]
    missing.extend(category for category, _ in priority_keywords if category not in all_categories)
    if missing:
        unique = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Category config references unknown categories: {unique}")

    VARIABLE_CATEGORIES[:] = variable_categories
    FIXED_CATEGORIES[:] = fixed_categories
    ALL_CATEGORIES[:] = all_categories
    CATEGORY_KEYWORDS.clear()
    CATEGORY_KEYWORDS.update(category_keywords)
    BILL_PRIORITY_KEYWORDS[:] = priority_keywords
    SHOPPING_KEYWORDS[:] = shopping_keywords
    SHOPPING_CATEGORIES.clear()
    SHOPPING_CATEGORIES.update(shopping_categories)
    CATEGORY_ALIASES.clear()
    CATEGORY_ALIASES.update(category_aliases)
    CONFIG_SOURCE = str(config.get("source") or "custom")


def category_config_status() -> dict[str, Any]:
    return {
        "source": CONFIG_SOURCE,
        "variable_count": len(VARIABLE_CATEGORIES),
        "fixed_count": len(FIXED_CATEGORIES),
        "categories": list(ALL_CATEGORIES),
    }


def category_guidance_text() -> str:
    parts: list[str] = []
    if BILL_PRIORITY_KEYWORDS:
        priority_parts = [
            f"{', '.join(keywords)} mean {category}"
            for category, keywords in BILL_PRIORITY_KEYWORDS
        ]
        parts.append("Category priority: " + "; ".join(priority_parts) + ".")
    if CATEGORY_ALIASES:
        alias_parts = [f"{alias} -> {category}" for alias, category in sorted(CATEGORY_ALIASES.items())]
        parts.append("Category aliases: " + "; ".join(alias_parts) + ".")
    return " ".join(parts)


CONFIG = _load_category_config()

VARIABLE_CATEGORIES = _string_list(CONFIG.get("variable_categories", []))
FIXED_CATEGORIES = _string_list(CONFIG.get("fixed_categories", []))
ALL_CATEGORIES = VARIABLE_CATEGORIES + FIXED_CATEGORIES
CATEGORY_KEYWORDS = _keyword_map(CONFIG.get("category_keywords", {}))
BILL_PRIORITY_KEYWORDS = _priority_keywords(CONFIG.get("priority_keywords", []))
SHOPPING_KEYWORDS = _string_list(CONFIG.get("shopping_keywords", []))
SHOPPING_CATEGORIES = _string_map(CONFIG.get("shopping_categories", {}))
CATEGORY_ALIASES = _string_map(CONFIG.get("category_aliases", {}))

_validate_config(CONFIG)
