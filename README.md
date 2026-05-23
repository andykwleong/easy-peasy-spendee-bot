# GetRichBot

GetRichBot is a private Telegram household expense bot that captures spending from a shared chat and appends confirmed raw expense rows to Google Sheets.

The bot keeps Telegram focused on quick capture, confirmation, edits, and deletion. Google Sheets remains the source of truth for raw data, category totals, monthly totals, charts, and reporting.

## How It Works

```text
Telegram group chat
  -> Railway-hosted Python bot
  -> optional OpenAI extraction for screenshots, voice notes, and natural-language actions
  -> Google Sheets raw expense log
  -> Google Sheets monthly summary
```

## Features

- Logs expenses from Telegram text messages.
- Maps each Telegram sender to either `Me` or `My wife`.
- Categorizes expenses using your private household category config.
- Appends every confirmed entry as raw data to Google Sheets.
- Supports pending review when category or amount is unclear.
- Supports duplicate detection before logging repeated expenses.
- Supports natural-language delete, edit, and question handling when OpenAI is configured.
- Supports screenshot and voice-note extraction with confirmation before logging.
- Supports monthly fixed expenses confirmation.
- Sends 9am Singapore month-end fixed expense reminders when `TELEGRAM_CHAT_ID` is set.
- Updates `Monthly Summary` with categories as rows and months as columns.
- Supports undo for the last expense sent by a user.
- Keeps the bot private to configured Telegram user IDs.

## Requirements

- Python 3.11
- Telegram bot token from [BotFather](https://t.me/BotFather)
- Google Sheet
- Google Cloud service account with Google Sheets API access
- Railway account for 24/7 hosting
- OpenAI API key for screenshots, voice notes, natural-language edits/deletes/questions, and richer extraction

## Google Sheet Tabs

Create a Google Sheet with these tabs:

### Raw Expenses

Header row:

```text
Entry ID,Timestamp,Date,Month,Logged By,Raw Input,Amount,Category,Description,Input Type,Status,Telegram Chat ID,Telegram Message ID
```

`Timestamp` is time only, for example `21:34:12`. `Date` stores the expense date.

### Fixed Expenses

Header row:

```text
Category,Default Amount,Active,Notes
```

Add your fixed expense categories here with default amounts. `Active` should be `TRUE` or `FALSE`.

### Monthly Summary

The bot can maintain this tab automatically. Rows are all fixed and non-fixed categories. Columns are months, for example `2026-05`, `2026-06`, and each cell is the total for that category and month. A `Total` row is added at the bottom.

If an unexpected month appears, for example `2023-05`, check `Raw Expenses` for a row with the wrong `Date` or `Month`. Fix the source row in `Raw Expenses`; do not only delete the column from `Monthly Summary`, because the bot rebuilds the summary from raw rows.

### Bot State

The bot creates this tab automatically when needed. It stores small markers so Railway restarts do not resend the same month-end reminder or final summary.

## Category Setup

Categories are user-specific and should stay private. The public repo includes [categories.example.json](categories.example.json) only as a safe template.

For local use:

1. Copy `categories.example.json` to `categories.json`.
2. Edit `categories.json` with your own categories and keywords.
3. Keep `CATEGORIES_FILE=categories.json` in `.env`.

For Railway:

1. Open your private `categories.json`.
2. Copy the full JSON content.
3. Paste it into a Railway variable named `CATEGORIES_JSON`.

`categories.json` is ignored by Git, so your real household category list is not committed.

Category config fields:

- `variable_categories` - normal expense categories users can log from Telegram.
- `fixed_categories` - monthly recurring categories shown in the `Fixed Expenses` sheet.
- `category_keywords` - words that help the bot choose a category.
- `priority_keywords` - category rules that should win before general matching, for example baby-related items before shopping.
- `shopping_keywords` - words that count as shopping.
- `shopping_categories` - sender-based shopping categories for `me` and `wife`.
- `category_aliases` - short names users can say when confirming or editing categories.

Example:

```json
{
  "variable_categories": ["Food", "Groceries", "Utilities", "Shopping - Person A", "Shopping - Person B"],
  "fixed_categories": ["Rent or mortgage", "Subscriptions"],
  "category_keywords": {
    "Food": ["dinner", "lunch", "coffee"],
    "Utilities": ["electricity", "water bill"]
  },
  "priority_keywords": [
    {"category": "Utilities", "keywords": ["electricity", "water bill"]}
  ],
  "shopping_keywords": ["shopping", "shoes", "clothes"],
  "shopping_categories": {
    "me": "Shopping - Person A",
    "wife": "Shopping - Person B"
  },
  "category_aliases": {
    "electricity": "Utilities"
  }
}
```

The category names in your Google Sheet must match the names in your category config.

## Setup

1. Create a Telegram bot with BotFather and get the bot token.
   - If the bot is in a group chat, use BotFather to disable privacy mode so it can read normal expense messages.
2. Create a Google Cloud service account with Sheets API enabled.
3. Share your Google Sheet with the service account email.
4. Download the service account JSON file.
5. Copy `.env.example` to `.env` and fill in the values.
   - Local Mac setup: set `GOOGLE_SERVICE_ACCOUNT_FILE` to the JSON file path.
   - Railway setup: set `GOOGLE_SERVICE_ACCOUNT_JSON` to the full JSON file contents instead of using a file path.
   - For scheduled Railway reminders, set `TELEGRAM_CHAT_ID` to the group chat ID shown by `/whoami`.
6. Install dependencies:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

7. Run:

```bash
python -m getrichbot.bot
```

For Railway deployment, the repo includes `railway.json` with this start command:

```bash
python -u -m getrichbot.bot
```

Railway should use Python 3.11 from `runtime.txt`.

## Environment Variables

Create a local `.env` file from `.env.example` for local testing, and set the same values in Railway for deployment.

```bash
TELEGRAM_BOT_TOKEN=
GOOGLE_SHEET_ID=
GOOGLE_SERVICE_ACCOUNT_FILE=
GOOGLE_SERVICE_ACCOUNT_JSON=
CATEGORIES_FILE=categories.json
CATEGORIES_JSON=
ME_TELEGRAM_IDS=
WIFE_TELEGRAM_IDS=
ME_LABEL=Me
WIFE_LABEL=My wife
RAW_EXPENSES_SHEET=Raw Expenses
FIXED_EXPENSES_SHEET=Fixed Expenses
MONTHLY_SUMMARY_SHEET=Monthly Summary
BOT_STATE_SHEET=Bot State
TELEGRAM_CHAT_ID=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
```

Never commit real secrets. Keep them in Railway variables or your local `.env`.

## Telegram Usage

Send expenses in natural text:

```text
dinner 60
ntuc 82.30
singtel 45
uniqlo 120
food 60 yesterday
groceries 45 2026-05-18
food 60 19th May
food 60 May 19
food 60 19/5
snacks 4.50
```

You can also upload a receipt, payment, or banking screenshot if `OPENAI_API_KEY` is configured. The bot will extract the likely expense and ask for confirmation before logging it.

You can also send a voice note if `OPENAI_API_KEY` is configured. The bot will transcribe it, extract the likely expense, and ask for confirmation before logging it.

You can also send multiple entries for the same date:

```text
19th May
Food 60
groceries 50
personal care 20
```

Or one dated expense per line:

```text
19th May food 20.62
17th May food 22.54
16th May 25.18 food
14th May 24.10 food
```

Commands:

- `/help` - show usage guide
- `/start` - show bot help
- `/whoami` - show your Telegram numeric user ID for setup
- `/pending` - show entries needing confirmation
- `/summary` - show this month's checkpoint summary
- `/confirm <pending_id> <category>` - confirm a pending entry
- `/undo` - delete your latest logged row from Google Sheets
- `/fixed` - preview active fixed expenses
- `/confirmfixed` - append active fixed expenses for the current month
- `/categories` - show available categories

Plain-language shortcuts:

- `help`
- `commands`
- `undo last`
- `delete last`
- `delete e1a2b3`
- `confirm abc123`
- `confirm abc123 as Food`
- `confirm abc123 as Food on 2026-05-19`
- `summary`
- `summary this month`
- `summary last month`
- `confirm fixed`

With `OPENAI_API_KEY` configured, natural language actions also work:

- `delete the $15 food expense on 16th May`
- `how much was dinner last Friday?`
- `change the 13th May shopping amount from 200 to 180`
- `change date to yesterday` for pending screenshot/voice items
- `confirm all` for pending screenshot/voice items

The bot still validates actions against real `Entry ID` rows in Google Sheets before deleting or editing.

Delete requests ask for confirmation before removing a row. Reply `yes` to delete, or `cancel`.

When a new expense has the same date, amount, and category as an existing confirmed expense, the bot flags it as a possible duplicate. Reply `confirm` to log it anyway, or `cancel` to discard the new duplicate attempt.

## Monthly Automation

If Railway is running and `TELEGRAM_CHAT_ID` is set:

- On the last day of the month at 9am Singapore time, the bot sends a fixed expenses reminder.
- Reply `confirm fixed` to add active fixed expenses to `Raw Expenses`.
- Fixed expenses are dated on the last day of that month.
- On the 1st of each month at 9am Singapore time, the bot refreshes `Monthly Summary` and sends the previous month's final summary.
- The bot avoids adding the same fixed category twice for the same month.

## Notes

- Shopping can be logged into sender-specific shopping categories based on your category config.
- Priority keywords and aliases come from your category config.
- A bare entry ID like `1d9c9a` opens the delete confirmation for that expense, so it will not be mistaken for a $9 expense.
- Telegram summaries and the `Monthly Summary` tab are recalculated from `Raw Expenses`.
- If a wrong month appears in `Monthly Summary`, correct the relevant `Date` and `Month` cells in `Raw Expenses`, then let the bot refresh the summary.
- Do not commit `.env` or your Google service account JSON file to GitHub.

## Privacy And Security

This bot handles personal finance data. Before making a fork public or inviting other users, review:

- [SECURITY.md](SECURITY.md)
- [PRIVACY.md](PRIVACY.md)

Keep the bot in a private Telegram chat, configure only trusted Telegram user IDs, and avoid sending unnecessary sensitive details such as full card numbers or bank account numbers.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
