import unittest

from getrichbot import categories
from getrichbot.categories import configure_category_config
from getrichbot.models import ExpenseRecord
from getrichbot.sheets import keyword_upsert_actions
from getrichbot.shop_category import categorize_email_shop
from getrichbot.shop_category import remember_email_shop_correction
from getrichbot.shop_category import resolve_shop_category
from getrichbot.shop_lookup import lookup_shop_text
from getrichbot.shop_lookup import opensearch_text
from getrichbot.shop_lookup import wikipedia_search_url
from decimal import Decimal


def _snapshot() -> dict:
    return {
        "variable_categories": list(categories.VARIABLE_CATEGORIES),
        "fixed_categories": list(categories.FIXED_CATEGORIES),
        "category_keywords": {key: list(value) for key, value in categories.CATEGORY_KEYWORDS.items()},
        "priority_keywords": [
            {"category": category, "keywords": list(keywords)}
            for category, keywords in categories.BILL_PRIORITY_KEYWORDS
        ],
        "shopping_keywords": list(categories.SHOPPING_KEYWORDS),
        "shopping_categories": dict(categories.SHOPPING_CATEGORIES),
        "category_aliases": dict(categories.CATEGORY_ALIASES),
    }


class ShopCategoryTests(unittest.TestCase):
    def setUp(self):
        self.original = _snapshot()

    def tearDown(self):
        configure_category_config(self.original)

    def test_food_panda_matches_the_category_name_without_a_keyword_or_a_search(self):
        configure_category_config(
            {
                **self.original,
                "category_keywords": {"Groceries": ["supermarket"]},
                "priority_keywords": [],
                "category_aliases": {},
            }
        )
        queries = []

        result = resolve_shop_category(
            "fp*Food Panda",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            lambda query: queries.append(query),
        )

        self.assertEqual(result.category, "Food")
        self.assertEqual(result.learned_keyword, "")
        self.assertEqual(queries, [])

    def test_unknown_shop_is_looked_up_by_name_only_and_logged_to_an_existing_category(self):
        queries = []

        def lookup(query):
            queries.append(query)
            return "This place is a neighbourhood supermarket."

        result = resolve_shop_category(
            "fp*Sample Depot",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries", "Income - misc"),
            lookup,
        )

        self.assertEqual(queries, ["Sample Depot"])
        self.assertNotIn("1234", queries[0])
        self.assertNotIn("18.50", queries[0])
        self.assertEqual(result.category, "Groceries")
        self.assertEqual(result.learned_keyword, "sample depot")
        self.assertNotEqual(result.category, "Income - misc")

    def test_failed_lookup_uses_a_category_word_and_a_blank_lookup_asks(self):
        care = resolve_shop_category(
            "Care Clinic",
            "Me",
            "Me",
            "My wife",
            ("Personal care", "Transport"),
            lambda _query: None,
        )
        self.assertEqual(care.category, "Personal care")
        self.assertEqual(care.learned_keyword, "care clinic")

        queries = []

        def fail(query):
            queries.append(query)
            raise TimeoutError("lookup down")

        unknown = resolve_shop_category(
            "Zzzqq Plug",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            fail,
        )
        self.assertEqual(queries, ["Zzzqq Plug"])
        self.assertIsNone(unknown.category)
        self.assertEqual(unknown.learned_keyword, "")

    def test_ambiguous_category_words_do_not_invent_a_category(self):
        result = resolve_shop_category(
            "Alpha Place",
            "Me",
            "Me",
            "My wife",
            ("Alpha Goods", "Alpha Services"),
            lambda _query: None,
        )
        self.assertIsNone(result.category)

    def test_keyword_save_failure_still_returns_the_category(self):
        def save(_keyword, _category):
            raise RuntimeError("sheet down")

        category = categorize_email_shop(
            "fp*Sample Depot",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            lambda _query: "a supermarket",
            save,
        )
        self.assertEqual(category, "Groceries")

    def test_a_saved_shop_is_not_searched_again(self):
        categories.remember_priority_keyword("sample depot", "Groceries")
        queries = []
        result = resolve_shop_category(
            "fp*Sample Depot",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            lambda query: queries.append(query),
        )
        self.assertEqual(result.category, "Groceries")
        self.assertEqual(queries, [])

    def test_correcting_an_email_shop_updates_that_keyword_only(self):
        saved = []
        email = ExpenseRecord(
            row_number=2,
            entry_id="abc123",
            timestamp="09:00:00",
            expense_date="2026-10-05",
            month="2026-10",
            logged_by="Me",
            raw_input="email: Sample Depot",
            amount=Decimal("18.50"),
            category="Groceries",
            description="fp*Sample Depot",
            input_type="Email",
            status="Confirmed",
        )
        typed = ExpenseRecord(
            row_number=3,
            entry_id="def456",
            timestamp="09:00:00",
            expense_date="2026-10-05",
            month="2026-10",
            logged_by="Me",
            raw_input="dinner",
            amount=Decimal("10"),
            category="Food",
            description="dinner",
            input_type="Text",
            status="Confirmed",
        )

        remember_email_shop_correction(email, "Transport", lambda keyword, category: saved.append((keyword, category)))
        remember_email_shop_correction(typed, "Transport", lambda keyword, category: saved.append((keyword, category)))

        self.assertEqual(saved, [("sample depot", "Transport")])

    def test_keyword_sheet_adds_once_and_then_updates_the_same_shop(self):
        added = keyword_upsert_actions([], "sample depot", "Groceries")
        self.assertEqual(added, [("append", 0, ["sample depot", "Groceries", "Priority", "TRUE"])])
        updated = keyword_upsert_actions(
            [["sample depot", "Groceries", "Priority", "TRUE"], ["sample depot", "Food", "Normal", "TRUE"]],
            "Sample Depot",
            "Transport",
        )
        self.assertEqual(
            updated,
            [
                ("update", 2, ["Transport", "Priority"]),
                ("update", 3, ["Transport", "Priority"]),
            ],
        )

    def test_lookup_url_contains_only_the_shop_name(self):
        url = wikipedia_search_url("Sample Depot")
        self.assertIn("search=Sample+Depot", url)
        self.assertNotIn("1234", url)
        self.assertNotIn("18.50", url)
        self.assertNotIn("SGD", url)
        self.assertIsNone(opensearch_text('["Sample Depot", [], [], []]'))
        self.assertIn("supermarket", opensearch_text('["Sample Depot", ["Sample Depot"], ["a supermarket"], ["https://example.com"]]'))

        seen = []

        def fetch(url):
            seen.append(url)
            raise TimeoutError("no network in tests")

        self.assertIsNone(lookup_shop_text("Sample Depot", fetch))
        self.assertEqual(len(seen), 1)
        self.assertIn("Sample+Depot", seen[0])
        self.assertNotIn("1234", seen[0])
