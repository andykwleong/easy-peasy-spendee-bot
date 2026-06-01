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

Categories are user-specific and should stay private. The recommended setup is to store categories in your Google Sheet so you can edit them without changing code or Railway variables.

Create a `Categories` tab:

```text
Category,Type,Active,Shopping Owner
Food,Variable,TRUE,
Groceries,Variable,TRUE,
Shopping - Person A,Variable,TRUE,me
Shopping - Person B,Variable,TRUE,wife
Rent or mortgage,Fixed,TRUE,
```

Create a `Category Keywords` tab:

```text
Keyword,Category,Priority,Active
dinner,Food,Normal,TRUE
lunch,Food,Normal,TRUE
grocery,Groceries,Normal,TRUE
electricity,Utilities,Priority,TRUE
shopping,Shopping - Sender,Normal,TRUE
```

`Shopping - Sender` is special. It means the bot should use the category marked `me` or `wife` in the `Shopping Owner` column.

`Priority` keywords are checked before normal shopping/generic matching. Use them for important overrides, such as baby-related items or specific utility bills.

The category names in `Raw Expenses`, `Fixed Expenses`, `Categories`, and `Category Keywords` should match exactly.

Category loading order:

1. The bot first tries the Google Sheet tabs named by `CATEGORIES_SHEET` and `CATEGORY_KEYWORDS_SHEET`.
2. If those tabs are missing or empty, it can fall back to `CATEGORIES_JSON`.
3. If `CATEGORIES_JSON` is also empty, it can fall back to a local categories file or the public example defaults.

Leaving `CATEGORIES_JSON` in Railway is fine as a backup, but the Google Sheet tabs are the main setup. If `/categories` shows old categories, check that the Sheet tabs are named correctly and that any old Railway fallback value is not hiding a Sheet setup problem.

The public repo still includes [categories.example.json](categories.example.json) as a fallback/template for open-source users who do not want to use Google Sheet category tabs.

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
CATEGORIES_SHEET=Categories
CATEGORY_KEYWORDS_SHEET=Category Keywords
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

For categories, most users should only set `CATEGORIES_SHEET` and `CATEGORY_KEYWORDS_SHEET`. `CATEGORIES_FILE` and `CATEGORIES_JSON` are optional fallback options for people who do not want to manage categories in Google Sheets.

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
groceries 63 and 15.20
```

`groceries 63 and 15.20` logs two separate grocery rows for today. This is not bill splitting; it is just a quick way to enter multiple separate expenses under the same category.

You can also upload a receipt, payment, or banking screenshot if `OPENAI_API_KEY` is configured. The bot will extract the likely expense and ask for confirmation before logging it.

You can also send a voice note if `OPENAI_API_KEY` is configured. The bot will transcribe it, extract the likely expense, and ask for confirmation before logging it.

You can also send multiple entries for the same date:

```text
19th May
Food 60
groceries 50
personal care 20
```

The first line can be a standalone date. Every expense line below it inherits that date:

```text
29th May
coffee 8.39
lunch 27.50
breakfast 8.60
```

Or one dated expense per line:

```text
19th May food 20.62
17th May food 22.54
16th May 25.18 food
14th May 24.10 food
```

Or one undated expense per line. These default to today:

```text
Dinner 83.93
Dessert 22.54
```

Each line is logged as its own row. The bot uses your category keywords to decide the category, so words like `dessert` only work when they are included in your private category config.

Commands:

- `/help` - show usage guide
- `/start` - show bot help
- `/whoami` - show your Telegram numeric user ID for setup
- `/pending` - show entries needing confirmation
- `/summary` - show this month's checkpoint summary
- `/confirm <pending_id> <category>` - confirm a pending entry
- `/undo` - delete your latest logged row from Google Sheets
- `/fixed` - preview active fixed expenses
- `/confirmfixed` - review active fixed expenses before adding them
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
- `confirm 2`
- `confirm 2 as Gifts`
- `gift`
- `change spend date to 21 May`
- `summary`
- `summary this month`
- `summary last month`
- `confirm fixed`
- `confirm fixed last month`
- `confirm fixed May 2026`

With `OPENAI_API_KEY` configured, natural language actions also work:

- `delete the $15 food expense on 16th May`
- `how much was dinner last Friday?`
- `change the 13th May shopping amount from 200 to 180`
- `change date to yesterday` for pending screenshot/voice items
- `confirm all` for pending screenshot/voice items

The bot still validates actions against real `Entry ID` rows in Google Sheets before deleting or editing.

Delete requests ask for confirmation before removing a row. Reply `yes` to delete, or `cancel`.

Edit requests for already logged Google Sheet rows also ask for confirmation before changing the row. Reply `yes` to update, or `cancel`.

When a new expense has the same date, amount, and category as an existing confirmed expense, the bot flags it as a possible duplicate. Reply `confirm` to log it anyway, or `cancel` to discard the new duplicate attempt.

Screenshot and voice-note entries stay pending until you confirm them. If you change a pending screenshot or voice-note item, for example `change 3 to Groceries and change 5 to Food`, the bot updates the pending list and asks you to confirm again.

When a new screenshot or voice note creates a pending list, plain replies like `confirm` and `confirm all` apply to the latest pending list only. Older pending items can still be handled by their specific pending IDs.

Duplicate checks use Google Sheets as the source of truth, plus a one-minute in-memory safety window for entries that were just logged but may not be visible in a read-back yet.

## Monthly Automation

If Railway is running and `TELEGRAM_CHAT_ID` is set:

- On the last day of the month at 9am Singapore time, the bot sends a fixed expenses review list.
- Reply `confirm fixed`, `confirmed fixed`, or `confirm` to add the reviewed fixed expenses to `Raw Expenses`.
- Fixed expenses are dated on the last day of that month.
- On the 1st of each month at 9am Singapore time, the bot refreshes `Monthly Summary` and sends the previous month's final summary.
- The bot logs every active row shown in the fixed expenses review.
- Fixed expense confirmation writes the reviewed fixed amounts directly into the matching month column in `Monthly Summary`.
- If the month column already exists, the reviewed fixed category values are replaced for that month.
- Fixed expenses are not handled with duplicate prompts. In `Monthly Summary`, the latest confirmed fixed value for a category/month is used instead of summing repeated fixed rows.

You can manually start a fixed expense review for a specific month:

```text
confirm fixed May 2026
confirm fixed last month
```

Before confirming, you can edit amounts using the category names shown in the list:

```text
income tax andy change to 30 and property tax hillview change to 10
```

The bot shows the full fixed expense list again after edits. Once you reply `confirm fixed`, `confirmed fixed`, or `confirm`, the rows are added to `Raw Expenses` for audit trail and the reviewed fixed values are written directly into `Monthly Summary`.

## Notes

- Shopping can be logged into sender-specific shopping categories based on your category config.
- Priority keywords and aliases come from your category config.
- A clear list of amounts under one category, such as `groceries 63 and 15.20`, logs as separate expense rows.
- Multiple undated expense lines default to today's date.
- Category changes should be made in the `Categories` and `Category Keywords` Google Sheet tabs.
- Follow-up replies can update pending entries, for example `gift` or `confirm 2 as Gifts`.
- `change spend date to 21 May` updates the latest logged expense for that sender.
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
