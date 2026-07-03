# Project Instructions

This file contains project-specific instructions for coding agents working on GetRichBot.

## Working With Andy

- Before building or changing code, show a short plan summary and ask for approval.
- Do not begin implementation until Andy clearly gives the go-ahead.
- Explain technical terms simply because Andy is not an engineer.
- If the objective, user flow, technical requirement, or business requirement is unclear, ask clarifying questions before proceeding.
- When giving an opinion or recommendation, explain the recommended option, why it is recommended, and important tradeoffs.

## Security

- Always protect secrets, API keys, tokens, passwords, private configuration, and service account files.
- Never commit `.env`, Google service account JSON files, Telegram bot tokens, OpenAI keys, or other private credentials.
- Keep `.gitignore` protections for local environment files, virtual environments, and service account JSON files.
- Use Railway environment variables for production secrets.
- For Railway Google Sheets auth, prefer `GOOGLE_SERVICE_ACCOUNT_JSON` over committing or uploading a credentials file to the repo.
- Before public-release documentation changes, check that `README.md`, `SECURITY.md`, `PRIVACY.md`, `LICENSE`, `.env.example`, and `.gitignore` do not expose private values.

## Integrations

- For third-party APIs, SDKs, and platforms, check official documentation before recommending or implementing material integration changes.
- Consider rate limits, cost controls, retries, logging, monitoring, data privacy, and hidden/background API calls.
- The bot currently integrates with Telegram, Google Sheets, OpenAI, GitHub, and Railway.

## Bot Behavior

- Google Sheets is the source of truth for logged expenses.
- `Raw Expenses` contains all confirmed transaction rows.
- `Fixed Expenses` contains active fixed expense setup.
- `Monthly Summary` is generated as a P&L summary; rows include income categories, expense categories, total income, total expenses, and net P&L.
- `Raw Expenses` includes a `Transaction Type` column after `Description`; valid values are `Expense`, `Income`, and `Fixed`.
- Old rows with blank `Transaction Type` are backward compatible: infer `Income` when the category starts with `Income -`, infer `Fixed` when input type is fixed, otherwise treat as `Expense`.
- If `Monthly Summary` shows an unexpected month, investigate and fix the source row in `Raw Expenses` instead of manually deleting the summary column.
- Date parsing must not treat decimal amounts as years. For example, `shopping 20th may 23.20` should resolve to the current/default year for `20 May`, not year 2023.
- `Bot State` stores small idempotency markers so Railway restarts do not duplicate scheduled reminders or final summaries.
- Fixed expenses should be dated on the last day of the relevant month.
- Scheduled monthly reminders/summaries run at 9am Singapore time when `TELEGRAM_CHAT_ID` is configured.
- Fixed expense confirmation is a review flow: show all fixed expenses first, allow amount edits by category name, then add rows to `Raw Expenses` only after `confirm fixed`.
- Fixed review amount edits should accept both `Category change to 30` and `change Category to 30`. A unique shortened category name may match, but ambiguous shortened names must not be guessed.
- `confirm fixed <month> <year>` and `confirm fixed last month` should review that target month, with rows dated on that month's last day.
- The fixed review list is the source for confirmation: if an active fixed expense row is shown, it should be inserted into `Raw Expenses` and written directly into `Monthly Summary`.
- Do not use duplicate prompts or duplicate skips for fixed expenses. Fixed expenses are monthly setup values with unique fixed categories.
- Before writing fixed rows for a confirmed month, delete existing confirmed fixed rows for that same month so the fixed audit trail stays clean.
- In `Monthly Summary`, fixed category/month values should be replaced by the latest confirmed fixed review amount, not summed as duplicate fixed rows.
- Income categories should start with `Income -`; this prefix is used to separate income from expenses in summaries.
- Income is not person-specific. The sender may still be recorded in `Logged By`, but P&L totals do not split income by sender.
- A typed generic income entry with a clear amount/date but no specific income category should show buttons for active `Income -` categories. The submitting user can tap one to log immediately; other users must not be allowed to choose it.
- Income-category buttons use the existing temporary pending-entry memory and are lost if Railway restarts before selection. Do not add hidden persistence or expiry without calling it out in the implementation plan.
- Plain `confirm`, `confirmed`, `confirm fixed`, and `confirmed fixed` should all confirm an active fixed expense review.
- Delete operations must be confirmation-based. Show the matched expense and wait for explicit confirmation before deleting from Google Sheets.
- Existing logged expense edits must be confirmation-based. Show the before/after row and wait for explicit confirmation before updating Google Sheets.
- User-specific categories should live in the Google Sheet `Categories` and `Category Keywords` tabs; do not hardcode personal category lists in public source.
- Production category loading must use the Google Sheet `Categories` and `Category Keywords` tabs. Do not silently fall back to JSON/default categories in production.
- If Google Sheet categories are missing or empty at startup, fail loudly so public fallback categories cannot leak into `Monthly Summary`.
- `Monthly Summary` must only show categories from the current configured category list plus total rows. Do not add extra rows from unknown/raw categories.
- Use `/categorydebug` to show category source/counts when debugging category surprises.
- Use `/refreshcategories` after Google Sheet category edits to reload `Categories` and `Category Keywords` without redeploying Railway.
- Category priority keywords and aliases should come from the Google Sheet category tabs and should beat generic category matching.
- A message with one clear category and multiple listed amounts, for example `groceries 63 and 15.20`, should log separate rows for each amount. This is not split-bill behavior.
- Multiple undated expense lines should log as separate rows dated today.
- In multiline messages, a standalone first-line date should apply to all following expense lines before checking for individually dated lines.
- Date-like text such as `21 May` should be removed before amount selection, so `30 gifts spent on 21 May` logs `$30`, not `$21`.
- Follow-up replies should handle normal wording such as `confirm 2`, `gift`, and `change spend date to 21 May`.
- If a normal typed expense is pending only because the category is missing, replying with a valid category should log it immediately.
- Screenshot and voice-note pending entries must remain pending after category/date changes until the user explicitly confirms logging.
- Plain pending replies like `confirm`, `yes`, and `confirm all` should target the latest pending batch for that chat/user when one exists; otherwise they should fall back to normal text pending entries.
- Duplicate checks should include a one-minute recently logged in-memory window so immediately repeated confirmations are caught even before Google Sheets read-back reflects the append.
- A bare 6-character entry ID should be treated as a delete lookup, not parsed as an expense amount.

## Deployment

- Railway runs the production bot.
- Local development uses Python 3.11.
- Railway should use Python 3.11 via `runtime.txt`.
- Railway starts the bot with `python -u -m getrichbot.bot` via `railway.json`.
- Only one live bot process should run at a time. Stop the local bot when Railway is running.

## Public Repository

- The project license is AGPL-3.0-or-later.
- Keep public docs clear enough for non-engineers to understand setup, privacy, security, and data flow.
- Do not include Andy's real Telegram IDs, bot tokens, Google Sheet IDs, OpenAI keys, service account JSON, private category list, or private household finance data in examples.
- When adding public examples, use obviously fake placeholder values.

## Code Quality

- Follow existing project structure and patterns.
- Keep changes focused on the requested behavior.
- Add tests for new parsing, summary, scheduling, or data transformation logic where practical.
- Run the test suite before committing.
- Update `README.md` and `.env.example` when setup, commands, variables, or user-facing behavior changes.

## Git

- Commit only intended project files.
- Do a quick secret check before staging or committing.
- Push changes to GitHub when Andy asks or when Railway needs the latest code.
