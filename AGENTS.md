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

## Integrations

- For third-party APIs, SDKs, and platforms, check official documentation before recommending or implementing material integration changes.
- Consider rate limits, cost controls, retries, logging, monitoring, data privacy, and hidden/background API calls.
- The bot currently integrates with Telegram, Google Sheets, OpenAI, GitHub, and Railway.

## Bot Behavior

- Google Sheets is the source of truth for logged expenses.
- `Raw Expenses` contains all confirmed expense rows.
- `Fixed Expenses` contains active fixed expense setup.
- `Monthly Summary` is generated from `Raw Expenses`; rows are categories and columns are months.
- `Bot State` stores small idempotency markers so Railway restarts do not duplicate scheduled reminders or final summaries.
- Fixed expenses should be dated on the last day of the relevant month.
- Scheduled monthly reminders/summaries run at 9am Singapore time when `TELEGRAM_CHAT_ID` is configured.
- Avoid duplicate fixed expense inserts for the same category and month.

## Deployment

- Railway runs the production bot.
- Local development uses Python 3.11.
- Railway should use Python 3.11 via `runtime.txt`.
- Railway starts the bot with `python -u -m getrichbot.bot` via `railway.json`.
- Only one live bot process should run at a time. Stop the local bot when Railway is running.

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
