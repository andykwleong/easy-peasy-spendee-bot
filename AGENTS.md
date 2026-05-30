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
- `Raw Expenses` contains all confirmed expense rows.
- `Fixed Expenses` contains active fixed expense setup.
- `Monthly Summary` is generated from `Raw Expenses`; rows are categories and columns are months.
- If `Monthly Summary` shows an unexpected month, investigate and fix the source row in `Raw Expenses` instead of manually deleting the summary column.
- Date parsing must not treat decimal amounts as years. For example, `shopping 20th may 23.20` should resolve to the current/default year for `20 May`, not year 2023.
- `Bot State` stores small idempotency markers so Railway restarts do not duplicate scheduled reminders or final summaries.
- Fixed expenses should be dated on the last day of the relevant month.
- Scheduled monthly reminders/summaries run at 9am Singapore time when `TELEGRAM_CHAT_ID` is configured.
- Avoid duplicate fixed expense inserts for the same category and month.
- Delete operations must be confirmation-based. Show the matched expense and wait for explicit confirmation before deleting from Google Sheets.
- Existing logged expense edits must be confirmation-based. Show the before/after row and wait for explicit confirmation before updating Google Sheets.
- User-specific categories should live in the Google Sheet `Categories` and `Category Keywords` tabs; do not hardcode personal category lists in public source.
- Category loading order is Google Sheet tabs first, then `CATEGORIES_JSON`, then local/example fallback. A stale Railway `CATEGORIES_JSON` value can hide a Sheet tab setup issue, so mention this when debugging category surprises.
- Category priority keywords and aliases should come from the Google Sheet category tabs and should beat generic category matching.
- A message with one clear category and multiple listed amounts, for example `groceries 63 and 15.20`, should log separate rows for each amount. This is not split-bill behavior.
- Multiple undated expense lines should log as separate rows dated today.
- In multiline messages, a standalone first-line date should apply to all following expense lines before checking for individually dated lines.
- Date-like text such as `21 May` should be removed before amount selection, so `30 gifts spent on 21 May` logs `$30`, not `$21`.
- Follow-up replies should handle normal wording such as `confirm 2`, `gift`, and `change spend date to 21 May`.
- Screenshot and voice-note pending entries must remain pending after category/date changes until the user explicitly confirms logging.
- Plain pending replies like `confirm` and `confirm all` should target the latest pending batch for that chat/user, not older screenshot or voice-note leftovers.
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
