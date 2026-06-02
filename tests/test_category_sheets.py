import unittest

from getrichbot.sheets import SheetsClient


class FakeKeywordsClient(SheetsClient):
    def __init__(self):
        pass

    def _get_values_first_available(self, sheet_names, cell_range):
        if sheet_names[0] == "Categories":
            return sheet_names[0], [
                ["Food", "Variable", "TRUE", ""],
                ["Shopping - Person A", "Variable", "TRUE", "me"],
                ["Shopping - Person B", "Variable", "TRUE", "wife"],
                ["Hidden", "Variable", "FALSE", ""],
                ["Income - A", "Income", "TRUE", ""],
                ["Rent", "Fixed", "TRUE", ""],
            ]
        return sheet_names[0], [
            ["dinner", "Food", "Normal", "TRUE"],
            ["lunch", "Food", "Normal", "TRUE"],
            ["income a", "Income - A", "Normal", "TRUE"],
            ["shopping", "Shopping - Sender", "Normal", "TRUE"],
            ["rent", "Rent", "Priority", "TRUE"],
            ["ignored", "Food", "Normal", "FALSE"],
        ]


class TestCategorySheets(unittest.TestCase):
    def test_category_sheet_rows_become_config(self):
        config = FakeKeywordsClient().get_category_config("Categories", "Category Keywords")

        self.assertEqual(config["variable_categories"], ["Food", "Shopping - Person A", "Shopping - Person B", "Income - A"])
        self.assertEqual(config["fixed_categories"], ["Rent"])
        self.assertEqual(config["source"], "google_sheets")
        self.assertEqual(config["categories_sheet_loaded"], "Categories")
        self.assertEqual(config["keywords_sheet_loaded"], "Category Keywords")
        self.assertEqual(config["shopping_categories"], {"me": "Shopping - Person A", "wife": "Shopping - Person B"})
        self.assertEqual(config["category_keywords"]["Food"], ["dinner", "lunch"])
        self.assertEqual(config["category_keywords"]["Income - A"], ["income a"])
        self.assertEqual(config["category_aliases"]["dinner"], "Food")
        self.assertEqual(config["shopping_keywords"], ["shopping"])
        self.assertEqual(config["priority_keywords"], [{"category": "Rent", "keywords": ["rent"]}])


if __name__ == "__main__":
    unittest.main()
