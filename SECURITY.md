# Security Policy

## Reporting Security Issues

Please do not open a public GitHub issue for security vulnerabilities.

If you find a security issue, contact the maintainer privately. If no private contact is listed for the fork you are using, open a minimal public issue asking for a private security contact without sharing exploit details.

## Secrets

Never commit real secrets, tokens, API keys, service account files, or private Google Sheet IDs.

Sensitive values should live in Railway variables or a local `.env` file that is not committed:

- `TELEGRAM_BOT_TOKEN`
- `GOOGLE_SHEET_ID`
- `GOOGLE_SERVICE_ACCOUNT_FILE`
- `GOOGLE_SERVICE_ACCOUNT_JSON`
- `CATEGORIES_JSON`
- private `categories.json` files
- `ME_TELEGRAM_IDS`
- `WIFE_TELEGRAM_IDS`
- `TELEGRAM_CHAT_ID`
- `OPENAI_API_KEY`

Google service account JSON files are private credentials. Keep them outside the repo, share the Google Sheet directly with the service account email, and rotate the service account key if it is ever exposed.

If a Telegram bot token, OpenAI API key, or Google service account key is exposed, rotate it immediately in the relevant provider dashboard and redeploy Railway with the new value.

## Access Control

This bot is intended to be private. Configure only trusted Telegram user IDs in `ME_TELEGRAM_IDS` and `WIFE_TELEGRAM_IDS`.

For group chats, disable Telegram bot privacy mode only for the intended private household group. Do not add the bot to public groups.

## Data Handling

Expense data is stored in your Google Sheet. Screenshot and voice-note extraction may send image/audio-derived content to OpenAI when `OPENAI_API_KEY` is configured.

Avoid sending bank account numbers, card numbers, government IDs, or other unnecessary sensitive information to the bot.

## Supported Version

Security fixes are expected to land on the `main` branch.
