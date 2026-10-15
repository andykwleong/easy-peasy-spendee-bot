import unittest

from getrichbot import categories
from getrichbot.categories import configure_category_config
from getrichbot.models import ExpenseRecord
from getrichbot.sheets import keyword_deactivate_actions
from getrichbot.sheets import keyword_upsert_actions
from getrichbot.shop_category import apply_email_shop_correction
from getrichbot.shop_category import categorize_email_shop
from getrichbot.shop_category import category_from_keywords
from getrichbot.shop_category import purchase_category
from getrichbot.shop_category import record_shop_category_choice
from getrichbot.shop_category import remember_email_shop_correction
from getrichbot.shop_category import resolve_shop_category
from getrichbot.shop_category import resolve_shop_category_tap
from getrichbot.shop_category import shop_category_callback
from getrichbot.shop_category import shop_choices_from_state
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

    def test_gpay_uses_transport_and_is_not_looked_up(self):
        queries = []
        asked = []
        result = resolve_shop_category(
            "GPAY NETWORK (S) PTE L Singapore SGP",
            "Me",
            "Me",
            "My wife",
            ("Transport", "Food", "Income - misc"),
            lambda query: queries.append(query),
            lambda *args: asked.append(args) or "Income - misc",
        )
        self.assertEqual(result.category, "Transport")
        self.assertEqual(result.learned_keyword, "gpay network (s) pte l singapore sgp")
        self.assertEqual(queries, [])
        self.assertEqual(asked, [])

        missing = resolve_shop_category(
            "GPAY NETWORK (S) PTE L Singapore SGP",
            "Me",
            "Me",
            "My wife",
            ("Food", "Income - misc"),
            lambda query: queries.append(query),
            lambda *args: asked.append(args) or "Income - misc",
        )
        self.assertIsNone(missing.category)
        self.assertEqual(missing.learned_keyword, "")
        self.assertEqual(queries, [])
        self.assertEqual(asked, [])

    def test_grab_keyword_transport_is_used_when_the_name_is_different(self):
        configure_category_config(
            {
                **self.original,
                "variable_categories": ["Getting around", "Food"],
                "category_keywords": {"Getting around": ["transport"]},
                "priority_keywords": [],
                "shopping_categories": {},
                "category_aliases": {},
            }
        )
        queries = []
        result = resolve_shop_category(
            "Grab ride",
            "Me",
            "Me",
            "My wife",
            ("Getting around", "Food", "Income - misc"),
            lambda query: queries.append(query),
            lambda *_args: "Income - misc",
        )
        self.assertEqual(result.category, "Getting around")
        self.assertEqual(queries, [])

    def test_later_gpay_correction_saves_the_shop_keyword(self):
        saved = []
        email = ExpenseRecord(
            row_number=4,
            entry_id="abc124",
            timestamp="14:42:00",
            expense_date="2026-10-13",
            month="2026-10",
            logged_by="Me",
            raw_input="email: GPAY NETWORK (S) PTE L Singapore SGP",
            amount=Decimal("9.60"),
            category="",
            description="GPAY NETWORK (S) PTE L Singapore SGP",
            input_type="Email",
            status="Confirmed",
            transaction_type="Expense",
            payment_method="UOB Sample Visa",
        )
        remember_email_shop_correction(
            email,
            "Transport",
            lambda keyword, category: saved.append((keyword, category)),
        )
        self.assertEqual(saved, [("gpay network (s) pte l singapore sgp", "Transport")])

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

    def test_one_past_category_is_kept_and_the_shop_is_not_looked_up(self):
        calls = []
        saved = []
        stopped = []
        records = (
            _row("fp*Sample Stall", "Shopping - Alex"),
            _row("Sample Stall", "", entry_id="blank1", row_number=3),
            _row("Sample Stall", "Income - misc", entry_id="inc001", row_number=4, transaction_type="Income"),
            _row("Other Depot", "Home - Alex", entry_id="other1", row_number=5),
            _row("Sample Stall", "Shopping - Alex", entry_id="pend01", row_number=6, status="Pending"),
        )

        result = purchase_category(
            "A charge at Sample Stall",
            "Sample Stall",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"),
            lambda *args: calls.append(args),
            lambda *args: saved.append(args),
            records,
            lambda shop: stopped.append(shop),
        )

        self.assertEqual(result.category, "Shopping - Alex")
        self.assertEqual(result.choices, ())
        self.assertEqual(calls, [])
        self.assertEqual(saved, [])
        self.assertEqual(stopped, [])

    def test_mixed_shop_asks_with_past_categories_and_does_not_look_up(self):
        calls = []
        saved = []
        stopped = []
        records = (
            _row("Sample Stall", "Shopping - Alex", entry_id="aaa111"),
            _row("Sample Stall", "Home - Alex", entry_id="bbb222", row_number=3),
            _row("Sample Stall", "Baby bills - Alex", entry_id="ccc333", row_number=4),
        )

        result = purchase_category(
            "Food at Sample Stall",
            "Sample Stall",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex", "Food"),
            lambda *args: calls.append(args),
            lambda *args: saved.append(args),
            records,
            lambda shop: stopped.append(shop),
        )

        self.assertEqual(result.category, "")
        self.assertEqual(result.choices, ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"))
        self.assertEqual(calls, [])
        self.assertEqual(saved, [])
        self.assertEqual(stopped, ["Sample Stall"])

    def test_shop_with_no_past_rows_still_uses_the_keyword_and_lookup_path(self):
        self._use_sample_categories()
        keyworded = purchase_category(
            "A charge at Sample Stall",
            "Sample Stall",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"),
            lambda shop, logged_by, body="": category_from_keywords(shop, logged_by, "Alex", "Sam"),
            expense_records=(),
        )
        self.assertEqual(keyworded.category, "Shopping - Alex")
        self.assertEqual(keyworded.choices, ())

        calls = []

        def lookup(shop, logged_by, body=""):
            del logged_by, body
            calls.append(shop)
            return "Baby bills - Alex"

        looked_up = purchase_category(
            "A charge at Sample Depot",
            "Sample Depot",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"),
            lookup,
            expense_records=(),
        )
        self.assertEqual(calls, ["Sample Depot"])
        self.assertEqual(looked_up.category, "Baby bills - Alex")

        def fail():
            raise TimeoutError("sheet")

        fallback = purchase_category(
            "A charge at Sample Depot",
            "Sample Depot",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"),
            lambda *args: "Home - Alex",
            expense_records=fail,
        )
        self.assertEqual(fallback.category, "Home - Alex")

    def test_second_category_stops_the_keyword_and_the_next_charge_asks(self):
        self._use_sample_categories()
        sheets = _KeywordSheet()
        first = _row("Sample Stall", "Shopping - Alex", entry_id="aaa111", row_number=2)
        second = _row("Sample Stall", "Shopping - Alex", entry_id="bbb222", row_number=3)
        apply_email_shop_correction(sheets, "Category Keywords", second, "Home - Alex", (first, second))

        self.assertEqual(sheets.upserts, [])
        self.assertEqual(sheets.deactivated, [("Category Keywords", "sample stall")])
        self.assertEqual(
            keyword_deactivate_actions(
                [["sample stall", "Shopping - Alex", "Priority", "TRUE"]],
                "Sample Stall",
            ),
            [2],
        )
        self.assertIsNone(category_from_keywords("Sample Stall", "Alex", "Alex", "Sam"))

        calls = []
        later = purchase_category(
            "Another charge at Sample Stall",
            "Sample Stall",
            "Alex",
            ("Shopping - Alex", "Home - Alex", "Baby bills - Alex"),
            lambda *args: calls.append(args),
            expense_records=(first, _row("Sample Stall", "Home - Alex", entry_id="bbb222", row_number=3)),
        )
        self.assertEqual(calls, [])
        self.assertEqual(later.category, "")
        self.assertEqual(later.choices, ("Shopping - Alex", "Home - Alex"))

    def test_fix_tagging_one_email_row_still_saves_the_keyword(self):
        self._use_sample_categories()
        sheets = _KeywordSheet()
        only = _row("Sample Stall", "Shopping - Alex")
        apply_email_shop_correction(sheets, "Category Keywords", only, "Home - Alex", (only,))
        self.assertEqual(sheets.deactivated, [])
        self.assertEqual(sheets.upserts, [("Category Keywords", "sample stall", "Home - Alex")])

    def test_only_the_submitter_can_choose_and_the_tap_does_not_pin_the_shop(self):
        record = _row("Sample Stall", "", logged_by="Alex", entry_id="abc123")
        choices = ("Shopping - Alex", "Home - Alex", "Baby bills - Alex")
        chosen = resolve_shop_category_tap("abc123", 2, choices, record, "Alex")
        blocked = resolve_shop_category_tap("abc123", 2, choices, record, "Sam")
        self.assertEqual(chosen.status, "ok")
        self.assertEqual(chosen.category, "Baby bills - Alex")
        self.assertEqual(blocked.status, "wrong_person")
        callback = shop_category_callback(record.entry_id, 2)
        self.assertEqual(callback, "shopcat|abc123|2")
        self.assertLessEqual(len(callback.encode()), 64)
        self.assertEqual(shop_choices_from_state('["Shopping - Alex", "Home - Alex"]'), choices[:2])

        sheets = _KeywordSheet()
        record_shop_category_choice(sheets, "Raw Expenses", record, chosen.category)
        self.assertEqual(sheets.upserts, [])
        self.assertEqual(sheets.deactivated, [])
        self.assertEqual(sheets.updates, [{"category": "Baby bills - Alex", "transaction_type": "Expense"}])

    def _use_sample_categories(self):
        configure_category_config(
            {
                **self.original,
                "variable_categories": ["Shopping - Alex", "Home - Alex", "Baby bills - Alex"],
                "fixed_categories": [],
                "category_keywords": {"Shopping - Alex": ["sample stall"]},
                "priority_keywords": [{"category": "Shopping - Alex", "keywords": ["sample stall"]}],
                "shopping_keywords": [],
                "shopping_categories": {},
                "category_aliases": {"sample stall": "Shopping - Alex"},
            }
        )


def _row(description, category, **overrides):
    values = {
        "row_number": 2,
        "entry_id": "abc123",
        "timestamp": "09:00:00",
        "expense_date": "2026-10-05",
        "month": "2026-10",
        "logged_by": "Alex",
        "raw_input": f"email: {description}",
        "amount": Decimal("9.99"),
        "category": category,
        "description": description,
        "input_type": "Email",
        "status": "Confirmed",
        "transaction_type": "Expense",
    }
    values.update(overrides)
    return ExpenseRecord(**values)


class _KeywordSheet:
    def __init__(self):
        self.upserts = []
        self.deactivated = []
        self.updates = []

    def upsert_category_keyword(self, sheet_name, keyword, category):
        self.upserts.append((sheet_name, keyword, category))

    def deactivate_category_keyword(self, sheet_name, keyword):
        self.deactivated.append((sheet_name, keyword))

    def update_expense_record(self, sheet_name, row_number, **updates):
        del sheet_name, row_number
        self.updates.append(updates)
