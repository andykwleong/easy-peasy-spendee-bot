# GetRichBot

A Telegram household expense bot that captures spending from a group chat and appends raw expense rows to Google Sheets.

The bot keeps Telegram focused on capture and confirmation. Google Sheets remains the source of truth for category totals, monthly totals, charts, and reporting.

## Features

- Logs expenses from Telegram text messages.
- Maps each Telegram sender to either `Me` or `My wife`.
- Categorizes expenses using your household category list.
- Appends every confirmed entry as raw data to Google Sheets.
- Supports pending review when category or amount is unclear.
- Supports monthly fixed expenses confirmation.
- Sends 9am Singapore month-end fixed expense reminders when `TELEGRAM_CHAT_ID` is set.
- Updates `Monthly Summary` with categories as rows and months as columns.
- Supports undo for the last expense sent by a user.

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

### Bot State

The bot creates this tab automatically when needed. It stores small markers so Railway restarts do not resend the same month-end reminder or final summary.

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

- Shopping is automatically logged as `Shopping - Me` or `Shopping - My wife` based on who sent the message.
- Telegram summaries and the `Monthly Summary` tab are recalculated from `Raw Expenses`.
- Do not commit `.env` or your Google service account JSON file to GitHub.
