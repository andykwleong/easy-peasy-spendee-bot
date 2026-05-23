import unittest
from datetime import date
from decimal import Decimal

from getrichbot.categories import CATEGORY_ALIASES, SHOPPING_CATEGORIES
from getrichbot.parser import extract_standalone_date, parse_expense


class TestParser(unittest.TestCase):
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
