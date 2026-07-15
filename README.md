# GetRichBot

GetRichBot is a private Telegram household finance bot that captures spending and income from a shared chat and appends confirmed raw transaction rows to Google Sheets.

The bot keeps Telegram focused on quick capture, confirmation, edits, and deletion. Google Sheets remains the source of truth for raw data, category totals, monthly P&L, charts, and reporting.

## How It Works

```text
Telegram group chat
  -> Railway-hosted Python bot
  -> optional OpenAI extraction for screenshots, voice notes, and natural-language actions
  -> Google Sheets raw transaction log
  -> Google Sheets monthly P&L summary
```

## Features

- Logs expenses and income from Telegram text messages.
- Maps each Telegram sender to either `Me` or `My wife`.
- Categorizes expenses using your private household category config.
- Appends every confirmed entry as raw transaction data to Google Sheets.
- Supports pending review when category or amount is unclear.
- Supports duplicate detection before logging repeated expenses.
- Supports natural-language delete, edit, and question handling when OpenAI is configured.
- Supports screenshot and voice-note extraction with confirmation before logging.
- Supports monthly fixed expenses confirmation.
- Sends 9am Singapore month-end fixed expense reminders when `TELEGRAM_CHAT_ID` is set.
- Updates `Monthly Summary` as a month-by-month P&L with total income, total expenses, net P&L, and cumulative P&L.
- Records a payment method for normal expenses using personal card, Cash, and PayNow buttons.
- Tracks personal credit-card spend against configurable calendar-month or billing-cycle caps.
- Shows each sender their own expense history for a chosen date or date range.
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
Entry ID,Timestamp,Date,Month,Logged By,Raw Input,Amount,Category,Description,Payment Method,Transaction Type,Input Type,Status,Telegram Chat ID,Telegram Message ID
```

`Timestamp` is time only, for example `21:34:12`. `Date` stores the transaction date.

`Payment Method` is selected in Telegram for new normal expenses. Historical rows may be blank; they remain valid for monthly summaries but are not included in card tracking.

`Transaction Type` is one of:

- `Expense`
- `Income`
- `Fixed`

Existing old rows with a blank `Transaction Type` are still supported. The bot treats blank old rows as expenses unless the category starts with `Income -`.

### Fixed Expenses

Header row:

```text
Category,Default Amount,Active,Notes
```

Add your fixed expense categories here with default amounts. `Active` should be `TRUE` or `FALSE`.

### Monthly Summary

The bot can maintain this tab automatically. Rows are categories and P&L totals. Columns are months, for example `2026-05`, `2026-06`, and each cell is the total for that category and month.

The generated format is:

```text
Income categories
Total Income

Expense and fixed expense categories
Total Expenses
Net P&L
Cumulative P&L
```

Monthly values are formatted as dollar currency automatically when the bot rebuilds this tab.

If an unexpected month appears, for example `2023-05`, check `Raw Expenses` for a row with the wrong `Date` or `Month`. Fix the source row in `Raw Expenses`; do not only delete the column from `Monthly Summary`, because the bot rebuilds the summary from raw rows.

### Bot State

The bot creates this tab automatically when needed. It stores small markers so Railway restarts do not resend the same month-end reminder or final summary.

### Payment Methods

Create a `Payment Methods` tab. Each row is one payment method belonging to one person. The bot uses `Owner` to show only that person's buttons.

```text
Payment Method,Owner,Type,Cycle Type,Cycle Start Day,Active,Notes
Example Rewards Card,Me,Credit Card,Calendar,1,TRUE,
Example Rewards Card,My wife,Credit Card,Calendar,1,TRUE,
Cash,Me,Cash,Calendar,1,TRUE,
PayNow,My wife,PayNow,Calendar,1,TRUE,
```

Use `Calendar` with a start day of `1` for a calendar-month cap. Use `Billing` and the day the rewards cap resets for statement-cycle cards. For example, `17` means the cycle runs from the 17th to the 16th. `Notes` is optional and ignored by the bot.

### Card Limits

Create a `Card Limits` tab. Add a row only when a card has a cap you want to track. A card with no row here is still selectable and appears under `Uncapped` in the card summary. If you prefer to list every card here, leave `Limit Amount` blank for an uncapped card; it is treated the same as having no cap.

```text
Payment Method,Owner,Category,Limit Amount,Active
Example Rewards Card,Me,Food,750,TRUE
Example Rewards Card,Me,Groceries,750,TRUE
Example Rewards Card,My wife,All,1000,TRUE
```

The `Payment Method` and `Owner` pair must match `Payment Methods` exactly. `Category` must be an existing expense category, or `All` for an overall cap. A transaction can count towards both an overall `All` cap and a category-specific cap where both are configured.

## Category Setup

Categories are user-specific and should stay private. The recommended setup is to store categories in your Google Sheet so you can edit them without changing code or Railway variables.

Create a `Categories` tab:

```text
Category,Type,Active,Shopping Owner
Food,Variable,TRUE,
Groceries,Variable,TRUE,
Shopping - Person A,Variable,TRUE,me
Shopping - Person B,Variable,TRUE,wife
Income - A,Income,TRUE,
Income - FX,Income,TRUE,
Income - Misc,Income,TRUE,
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
income a,Income - A,Normal,TRUE
salary fx,Income - FX,Normal,TRUE
dividend,Income - Misc,Normal,TRUE
dividends,Income - Misc,Normal,TRUE
sale proceed,Income - Misc,Normal,TRUE
sales proceeds,Income - Misc,Normal,TRUE
interest,Income - Misc,Normal,TRUE
bonus,Income - Misc,Normal,TRUE
```

`Shopping - Sender` is special. It means the bot should use the category marked `me` or `wife` in the `Shopping Owner` column.

`Priority` keywords are checked before normal shopping/generic matching. Use them for important overrides, such as baby-related items or specific utility bills.

The category names in `Raw Expenses`, `Fixed Expenses`, `Categories`, and `Category Keywords` should match exactly.

Income category names should start with `Income -`. The bot uses that prefix to separate income from expenses in `Monthly Summary`.

If a typed entry says only `income` and does not identify a specific income category, the bot shows buttons for the active `Income -` categories. Tapping a button logs the pending income immediately. Only the person who submitted the entry can choose its category.

Production category loading:

1. The bot loads categories from the Google Sheet tabs named by `CATEGORIES_SHEET` and `CATEGORY_KEYWORDS_SHEET`.
2. If no active Google Sheet categories are found, the bot stops at startup instead of silently using public fallback categories.
3. Use `/categorydebug` in Telegram to confirm the loaded source and category counts.

Do not use `CATEGORIES_JSON` in Railway for the normal setup. Keeping categories in Google Sheets avoids stale fallback categories appearing in `Monthly Summary`.

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
PAYMENT_METHODS_SHEET=Payment Methods
CARD_LIMITS_SHEET=Card Limits
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

For categories, set `CATEGORIES_SHEET` and `CATEGORY_KEYWORDS_SHEET`. The running bot expects active category rows in Google Sheets.

Payment configuration is read only when you use the bot. It is cached in memory for one minute to keep payment buttons responsive. Use `/refreshpayments` after editing `Payment Methods` or `Card Limits` to apply a change immediately. No background payment-sheet polling occurs.

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

After a normal expense has a category, the bot asks which payment method was used. It only shows methods owned by the person who sent the expense. The expense is saved after a payment button is tapped. Income and fixed expenses do not ask for a payment method.

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
- `/cards` - show your current card spending and limits
- `/confirm <pending_id> <category>` - confirm a pending entry
- `/undo` - delete your latest logged row from Google Sheets
- `/fixed` - preview active fixed expenses
- `/confirmfixed` - review active fixed expenses before adding them
- `/categories` - show available categories
- `/refreshcategories` - reload `Categories` and `Category Keywords` from Google Sheets after you edit them
- `/refreshpayments` - reload `Payment Methods` and `Card Limits` from Google Sheets after you edit them

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
- `card summary`
- `expenses on 12 July`
- `expenses between 10-12 July`
- `expenses from 11 till 14 July`
- `confirm fixed`
- `confirm fixed last month`
- `confirm fixed May 2026`

With `OPENAI_API_KEY` configured, natural language actions also work:

- `delete the $15 food expense on 16th May`
- `how much was dinner last Friday?`
- `change the 13th May shopping amount from 200 to 180`
- `change date to yesterday` for pending screenshot/voice items
- `confirm all` for pending screenshot/voice items
- `confirm 1 and 3` to log only selected pending screenshot/voice items and discard the rest

The bot still validates actions against real `Entry ID` rows in Google Sheets before deleting or editing.

Delete requests ask for confirmation before removing a row. Reply `yes` to delete, or `cancel`.

Edit requests for already logged Google Sheet rows also ask for confirmation before changing the row. If a date edit needs the new date, the bot remembers the matched row while it waits for your next reply, such as `30 June 2026`. Reply `cancel` to discard that date change. This temporary state is cleared after the confirmation prompt or a Railway restart. Reply `yes` to the before/after confirmation to update the row.

If the bot asks for a missing category on a normal typed expense, reply with the category name. For example, if the bot asks about `durian 12`, replying `Food` logs it immediately.

For a generic entry such as `income 15 June 2026 15020.33`, the bot shows the active income categories as buttons. The amount and date are preserved, and selecting a button logs the income immediately. These pending buttons use temporary process memory and stop working if Railway restarts before a selection is made.

When a new expense has the same date, amount, and category as an existing confirmed expense, the bot flags it as a possible duplicate. Reply `confirm` to log it anyway, or `cancel` to discard the new duplicate attempt.

Screenshot and voice-note entries stay pending until you confirm them. If you change a pending screenshot or voice-note item, for example `change 3 to Groceries and change 5 to Food`, the bot updates the pending list and asks you to confirm again.

When pending screenshot or voice-note expenses exist, the bot blocks new expense logging and asks you to decide on the visible pending list first. Use `confirm all` to log all visible pending items, `confirm 1 and 3` to log only selected items and discard the rest, or `cancel` to discard the visible pending list.

Plain `yes` does not confirm a pending screenshot or voice-note batch. It is reserved for delete/edit confirmation prompts.

When a new screenshot or voice note creates a pending list, plain replies like `confirm` and `confirm all` apply to the latest pending list only. Older pending items can still be handled by their specific pending IDs.

Duplicate checks use Google Sheets as the source of truth, plus a one-minute in-memory safety window for entries that were just logged but may not be visible in a read-back yet.

If a duplicate is found while confirming a pending list, the bot stops at the first duplicate instead of continuing through the batch. Reply `confirm` to log that duplicate anyway, or `cancel` to skip it and continue deciding on the remaining pending items.

### Card Summary

Use `card summary` or `/cards` to see only your own active credit cards. Capped cards are grouped under `Capped:` and uncapped cards under `Uncapped:`. Cards with one limit show card, spend, cap, and percentage on one line. Cards with multiple limits show their category lines below the card name. Card-cycle dates are used for calculation but omitted from the message.

The cap marker is green below 60%, yellow from 60% to 79%, orange from 80% to 94%, and red at 95% or more.

### Personal Expense History

These requests show only normal expenses logged by the person asking. They exclude income and fixed expenses.

```text
expenses on 12 July
expenses between 10-12 July
expenses from 10 July to 12 July
expenses from 11 till 14 July
what did I key in on 12 July
```

## Monthly Automation

If Railway is running and `TELEGRAM_CHAT_ID` is set:

- On the last day of the month at 9am Singapore time, the bot sends a fixed expenses review list.
- Reply `confirm fixed`, `confirmed fixed`, or `confirm` to add the reviewed fixed expenses to `Raw Expenses`.
- Fixed expenses are dated on the last day of that month.
- On the 1st of each month at 9am Singapore time, the bot refreshes `Monthly Summary` and sends the previous month's final summary.
- The bot logs every active row shown in the fixed expenses review.
- Before writing fixed expenses for a month, the bot removes existing confirmed fixed rows for that same month, then writes the reviewed fixed rows again. This keeps the fixed audit trail clean.
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
change income tax andy to 30 and change property tax hillview to 10
```

Unique shortened names also work. For example, `change example provider to 64.02` can match `Bills (Example Provider)`. If a shortened name matches more than one fixed category, the bot asks you to use a more specific name instead of guessing.

The bot shows the full fixed expense list again after edits. Once you reply `confirm fixed`, `confirmed fixed`, or `confirm`, the rows are added to `Raw Expenses` for audit trail and the reviewed fixed values are written directly into `Monthly Summary`.

## Notes

- Shopping can be logged into sender-specific shopping categories based on your category config.
- Priority keywords and aliases come from your category config.
- A clear list of amounts under one category, such as `groceries 63 and 15.20`, logs as separate expense rows.
- Multiple undated expense lines default to today's date.
- Category changes should be made in the `Categories` and `Category Keywords` Google Sheet tabs. After editing those tabs, send `/refreshcategories` in Telegram so the running Railway bot reloads the latest sheet values.
- Payment methods and limits should be changed in `Payment Methods` and `Card Limits`. After editing either tab, send `/refreshpayments` to load the change immediately. Otherwise, the bot keeps the current payment configuration for up to one minute after a read.
- Payment selection is temporary while the bot is running. If Railway restarts before you tap a payment button, resend the expense instead of assuming it was logged.
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
