import unittest

from getrichbot.bot import FinanceBot


class FakeSettings:
    raw_expenses_sheet = "Raw Expenses"
    monthly_summary_sheet = "Monthly Summary"
    me_label = "Me"
    wife_label = "My wife"

    def label_for_user(self, telegram_user_id):
        return "Me" if telegram_user_id == 123 else None


class FakeSheets:
    def __init__(self):
        self.rows = []

    def append_expense(self, sheet_name, row):
        self.rows.append(row)

    def get_expense_records(self, sheet_name):
        return []

    def update_monthly_summary(self, sheet_name, rows):
        pass


class FakeUser:
    id = 123


class FakeChat:
    id = -100


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.message_id = 456
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class TestMultiline(unittest.IsolatedAsyncioTestCase):
    async def test_logs_one_dated_expense_per_line(self):
        sheets = FakeSheets()
        bot = FinanceBot(FakeSettings(), sheets)
        update = FakeUpdate(
            "19th may 2026 food 20.62\n"
            "17th may 2026 food 22.54\n"
            "16th may 2026 25.18 food\n"
            "14th may 2026 24.1 food"
        )

        handled = await bot.handle_multiline_text(update, context=None)

        self.assertTrue(handled)
        self.assertEqual(len(sheets.rows), 4)
        self.assertEqual([str(row.amount) for row in sheets.rows], ["20.62", "22.54", "25.18", "24.1"])
        self.assertEqual([row.timestamp.date().isoformat() for row in sheets.rows], [
            "2026-05-19",
            "2026-05-17",
            "2026-05-16",
            "2026-05-14",
        ])
        self.assertTrue(all(row.category == "Food" for row in sheets.rows))


if __name__ == "__main__":
    unittest.main()
