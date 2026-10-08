import unittest

from getrichbot import categories
from getrichbot.categories import configure_category_config
from getrichbot.models import ExpenseRecord
from getrichbot.sheets import keyword_upsert_actions
from getrichbot.shop_category import categorize_email_shop
from getrichbot.shop_category import remember_email_shop_correction
from getrichbot.shop_category import resolve_shop_category
from getrichbot.shop_lookup import EXA_SEARCH_URL
from getrichbot.shop_lookup import exa_description
from getrichbot.shop_lookup import exa_search_body
from getrichbot.shop_lookup import lookup_shop_text
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

        asked = []

        def ask(shop, description, choices):
            asked.append((shop, description, choices))
            if "supermarket" in description:
                return "Groceries"
            return ""

        result = resolve_shop_category(
            "fp*Sample Depot",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries", "Income - misc"),
            lookup,
            ask,
        )

        self.assertEqual(queries, ["Sample Depot"])
        self.assertNotIn("1234", queries[0])
        self.assertNotIn("18.50", queries[0])
        self.assertEqual(asked[0][0], "Sample Depot")
        self.assertIn("supermarket", asked[0][1])
        self.assertNotIn("18.50", asked[0][1])
        self.assertNotIn("1234", asked[0][1])
        self.assertEqual(result.category, "Groceries")
        self.assertEqual(result.learned_keyword, "sample depot")
        self.assertNotEqual(result.category, "Income - misc")

    def test_failed_lookup_leaves_the_category_blank_and_does_not_ask(self):
        asked = []
        care = resolve_shop_category(
            "Care Clinic",
            "Me",
            "Me",
            "My wife",
            ("Personal care", "Transport"),
            lambda _query: None,
            lambda *args: asked.append(args),
        )
        self.assertIsNone(care.category)
        self.assertEqual(care.learned_keyword, "")
        self.assertEqual(asked, [])

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
            lambda *args: asked.append(args),
        )
        self.assertEqual(queries, ["Zzzqq Plug"])
        self.assertIsNone(unknown.category)
        self.assertEqual(unknown.learned_keyword, "")
        self.assertEqual(asked, [])

    def test_model_cannot_invent_a_category(self):
        cannot = resolve_shop_category(
            "Alpha Place",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            lambda _query: "a small shop",
            lambda *_args: "cannot",
        )
        invented = resolve_shop_category(
            "Alpha Place",
            "Me",
            "Me",
            "My wife",
            ("Food", "Groceries"),
            lambda _query: "a small shop",
            lambda *_args: "Made Up",
        )
        self.assertIsNone(cannot.category)
        self.assertIsNone(invented.category)
        self.assertEqual(cannot.learned_keyword, "")

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
            lambda *_args: "Groceries",
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

    def test_exa_search_sends_only_the_shop_name(self):
        body = exa_search_body("Sample Depot")
        encoded = str(body)
        self.assertEqual(body["query"], "Sample Depot")
        self.assertEqual(body["numResults"], 1)
        self.assertNotIn("1234", encoded)
        self.assertNotIn("18.50", encoded)
        self.assertNotIn("SGD", encoded)
        self.assertIsNone(exa_description('{"results": []}'))
        self.assertIsNone(exa_description('{"results": [{"title": "", "highlights": []}]}'))
        self.assertIn(
            "supermarket",
            exa_description('{"results": [{"title": "Sample Depot", "highlights": ["a supermarket"]}]}'),
        )

        seen = []

        def fetch(url, payload, api_key):
            seen.append((url, payload, api_key))
            raise TimeoutError("no network in tests")

        self.assertIsNone(lookup_shop_text("Sample Depot", fetch=fetch))
        self.assertEqual(seen, [])
        self.assertIsNone(lookup_shop_text("Sample Depot", "test-exa-key", fetch=fetch))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], EXA_SEARCH_URL)
        self.assertEqual(seen[0][1]["query"], "Sample Depot")
        self.assertNotIn("1234", str(seen[0][1]))
        self.assertNotIn("test-exa-key", seen[0][0])
        self.assertNotIn("test-exa-key", str(seen[0][1]))
