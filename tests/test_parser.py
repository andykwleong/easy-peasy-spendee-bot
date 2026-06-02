import unittest
from datetime import date
from decimal import Decimal

from getrichbot import categories
from getrichbot.categories import CATEGORY_ALIASES, SHOPPING_CATEGORIES, configure_category_config
from getrichbot.parser import extract_standalone_date, parse_expense, parse_expenses


class TestParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_config = {
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
        test_config = {
            **cls.original_config,
            "variable_categories": [
                *cls.original_config["variable_categories"],
                *[
                    category
                    for category in ["Income - A", "Income - FX", "Income - Misc"]
                    if category not in cls.original_config["variable_categories"]
                ],
            ],
            "category_keywords": {
                **cls.original_config["category_keywords"],
                "Income - A": ["income a", "salary a"],
                "Income - FX": ["income fx", "salary fx"],
                "Income - Misc": ["dividend", "dividends", "sale proceed", "sales proceeds", "interest", "bonus"],
            },
            "category_aliases": {
                **cls.original_config["category_aliases"],
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
        configure_category_config(test_config)

    @classmethod
    def tearDownClass(cls):
        configure_category_config(cls.original_config)

    def test_parse_food_expense(self):
        draft = parse_expense("dinner 60", "Me", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("60"))
        self.assertEqual(draft.category, "Food")
        self.assertEqual(draft.description, "dinner")

    def test_parse_snacks_as_food(self):
        draft = parse_expense("21 may snacks 4.5", "Me", "Me", "My wife", today=date(2026, 5, 22))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("4.5"))
        self.assertEqual(draft.category, "Food")
        self.assertEqual(draft.expense_date, date(2026, 5, 21))

    def test_parse_tea_as_food(self):
        draft = parse_expense("tea 3.5", "Me", "Me", "My wife", today=date(2026, 5, 22))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, "Food")

    def test_parse_groceries_expense(self):
        draft = parse_expense("ntuc $82.30", "Me", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("82.30"))
        self.assertEqual(draft.category, "Groceries")

    def test_parse_income_categories(self):
        examples = {
            "income a 5000": "Income - A",
            "salary fx 7000": "Income - FX",
            "dividend 120": "Income - Misc",
            "dividends 120": "Income - Misc",
            "sales proceeds 800": "Income - Misc",
            "interest 30": "Income - Misc",
            "bonus 1000": "Income - Misc",
        }

        for text, expected_category in examples.items():
            with self.subTest(text=text):
                draft = parse_expense(text, "Me", "Me", "My wife", today=date(2026, 6, 2))
                self.assertIsNotNone(draft)
                self.assertEqual(draft.category, expected_category)

    def test_parse_same_category_multiple_amounts(self):
        drafts = parse_expenses("Groceries 63 and 15.2", "Me", "Me", "My wife", today=date(2026, 5, 24))

        self.assertEqual(len(drafts), 2)
        self.assertEqual([draft.amount for draft in drafts], [Decimal("63"), Decimal("15.2")])
        self.assertTrue(all(draft.category == "Groceries" for draft in drafts))
        self.assertTrue(all(draft.expense_date == date(2026, 5, 24) for draft in drafts))

    def test_does_not_split_count_and_amount(self):
        drafts = parse_expenses("dinner for 2 60", "Me", "Me", "My wife", today=date(2026, 5, 24))

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].amount, Decimal("60"))
        self.assertEqual(drafts[0].category, "Food")

    def test_date_is_removed_before_amount_selection(self):
        draft = parse_expense("30 gifts spent on 21 may", "Me", "Me", "My wife", today=date(2026, 5, 24))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("30"))
        self.assertEqual(draft.category, "Gifts")
        self.assertEqual(draft.expense_date, date(2026, 5, 21))

    def test_parse_shopping_for_me(self):
        draft = parse_expense("uniqlo 120", "Me", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, SHOPPING_CATEGORIES["me"])

    def test_parse_shopping_for_wife(self):
        draft = parse_expense("uniqlo 120", "My wife", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, SHOPPING_CATEGORIES["wife"])

    def test_baby_beats_shopping_keywords(self):
        draft = parse_expense("$80 baby shoes", "My wife", "Me", "My wife", today=date(2026, 5, 23))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("80"))
        self.assertEqual(draft.category, CATEGORY_ALIASES["baby"])

    def test_baby_beats_generic_bills(self):
        draft = parse_expense("$80 bills baby", "My wife", "Me", "My wife", today=date(2026, 5, 23))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, CATEGORY_ALIASES["baby"])

    def test_bills_keywords_map_to_specific_categories(self):
        examples = {
            "electricity bills 120": CATEGORY_ALIASES["electricity"],
            "insurance 300": CATEGORY_ALIASES["insurance"],
        }
        if "sp bills" in CATEGORY_ALIASES:
            examples["SP bills 120"] = CATEGORY_ALIASES["sp bills"]
        if "singtel" in CATEGORY_ALIASES:
            examples["singtel 80"] = CATEGORY_ALIASES["singtel"]
        if "ar;yn" in CATEGORY_ALIASES:
            examples["ar;yn 55"] = CATEGORY_ALIASES["ar;yn"]
        if "misc bills" in CATEGORY_ALIASES:
            examples["misc bills 10"] = CATEGORY_ALIASES["misc bills"]

        for text, expected_category in examples.items():
            with self.subTest(text=text):
                draft = parse_expense(text, "Me", "Me", "My wife", today=date(2026, 5, 23))
                self.assertIsNotNone(draft)
                self.assertEqual(draft.category, expected_category)

    def test_entry_id_is_not_parsed_as_expense(self):
        self.assertIsNone(parse_expense("1d9c9a", "My wife", "Me", "My wife", today=date(2026, 5, 23)))

    def test_unknown_category_becomes_pending_candidate(self):
        draft = parse_expense("random merchant 12", "Me", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("12"))
        self.assertIsNone(draft.category)

    def test_parse_yesterday(self):
        draft = parse_expense("food 60 yesterday", "Me", "Me", "My wife", today=date(2026, 5, 20))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("60"))
        self.assertEqual(draft.category, "Food")
        self.assertEqual(draft.description, "food")
        self.assertEqual(draft.expense_date, date(2026, 5, 19))

    def test_parse_iso_date(self):
        draft = parse_expense("groceries 45 2026-05-18", "Me", "Me", "My wife")

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, "Groceries")
        self.assertEqual(draft.description, "groceries")
        self.assertEqual(draft.expense_date, date(2026, 5, 18))

    def test_parse_day_month_date(self):
        draft = parse_expense("food 60 19th may", "Me", "Me", "My wife", today=date(2026, 5, 20))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.category, "Food")
        self.assertEqual(draft.description, "food")
        self.assertEqual(draft.expense_date, date(2026, 5, 19))

    def test_day_month_before_decimal_amount_does_not_treat_amount_as_year(self):
        draft = parse_expense("16th may 25.18 food", "Me", "Me", "My wife", today=date(2026, 5, 22))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.amount, Decimal("25.18"))
        self.assertEqual(draft.category, "Food")
        self.assertEqual(draft.expense_date, date(2026, 5, 16))

    def test_parse_month_day_date(self):
        draft = parse_expense("food 60 may 19", "Me", "Me", "My wife", today=date(2026, 5, 20))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.expense_date, date(2026, 5, 19))

    def test_parse_slash_date(self):
        draft = parse_expense("food 60 19/5", "Me", "Me", "My wife", today=date(2026, 5, 20))

        self.assertIsNotNone(draft)
        self.assertEqual(draft.expense_date, date(2026, 5, 19))

    def test_ambiguous_slash_date_needs_confirmation(self):
        draft = parse_expense("food 60 5/6", "Me", "Me", "My wife", today=date(2026, 5, 20))

        self.assertIsNotNone(draft)
        self.assertTrue(draft.needs_date_confirmation)

    def test_extract_standalone_date(self):
        parsed, ambiguous = extract_standalone_date("19th May", today=date(2026, 5, 20))

        self.assertFalse(ambiguous)
        self.assertEqual(parsed, date(2026, 5, 19))


if __name__ == "__main__":
    unittest.main()
