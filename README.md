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

Use formulas or pivot tables based on `Raw Expenses`.

## Setup

1. Create a Telegram bot with BotFather and get the bot token.
   - If the bot is in a group chat, use BotFather to disable privacy mode so it can read normal expense messages.
2. Create a Google Cloud service account with Sheets API enabled.
3. Share your Google Sheet with the service account email.
4. Download the service account JSON file.
5. Copy `.env.example` to `.env` and fill in the values.
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

Commands:

- `/help` - show usage guide
- `/start` - show bot help
- `/whoami` - show your Telegram numeric user ID for setup
- `/pending` - show entries needing confirmation
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

With `OPENAI_API_KEY` configured, natural language actions also work:

- `delete the $15 food expense on 16th May`
- `how much was dinner last Friday?`
- `change the 13th May shopping amount from 200 to 180`
- `change date to yesterday` for pending screenshot/voice items
- `confirm all` for pending screenshot/voice items

The bot still validates actions against real `Entry ID` rows in Google Sheets before deleting or editing.

## Monthly Summary Formulas

In `Monthly Summary`, you can summarize raw rows directly from `Raw Expenses`.

Category totals:

```text
=QUERY('Raw Expenses'!D:H,"select D,H,sum(G) where K='Confirmed' group by D,H label sum(G) 'Total'",1)
```

Monthly totals:

```text
=QUERY('Raw Expenses'!D:G,"select D,sum(G) where D is not null group by D label sum(G) 'Total Spend'",1)
```

## Notes

- Shopping is automatically logged as `Shopping - Me` or `Shopping - My wife` based on who sent the message.
- The bot does not calculate monthly totals in Telegram.
- Google Sheets formulas or pivots should perform all summation.
