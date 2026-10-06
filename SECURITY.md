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
- private category setup in Google Sheets
- private payment method and card limit setup in Google Sheets
- `ME_TELEGRAM_IDS`
- `WIFE_TELEGRAM_IDS`
- `TELEGRAM_CHAT_ID`
- `OPENAI_API_KEY`
- `GMAIL_OAUTH_CLIENT_ID`
- `GMAIL_OAUTH_CLIENT_SECRET`
- `GMAIL_OAUTH_REFRESH_TOKEN`
- `GMAIL_PUBSUB_TOPIC`
- `ME_FORWARDER_EMAIL`
- `WIFE_FORWARDER_EMAIL`

Google service account JSON files are private credentials. Keep them outside the repo, share the Google Sheet directly with the service account email, and rotate the service account key if it is ever exposed.

If a Telegram bot token, OpenAI API key, or Google service account key is exposed, rotate it immediately in the relevant provider dashboard and redeploy Railway with the new value.

The application suppresses routine HTTP client request logs and redacts Telegram bot tokens from formatted log messages. Treat this as defence in depth: if a token appeared in logs before this protection was deployed, revoke the old token through BotFather and replace `TELEGRAM_BOT_TOKEN` in Railway.

If your private category config is exposed, remove it from the public repo and review your Google Sheet sharing settings. Category names are not usually credentials, but they can still reveal personal household information.

If your payment method or card limit setup is exposed, review whether the names reveal personal banking information. Do not store full card numbers in `Payment Methods`, `Card Limits`, Telegram messages, or docs.

Category setup should normally live in the private Google Sheet `Categories` and `Category Keywords` tabs. Keeping `CATEGORIES_JSON` in Railway is acceptable as a private fallback, but treat it like sensitive configuration, keep it updated if you use it, and remove it if it becomes stale or confusing.

## Access Control

This bot is intended to be private. Configure only trusted Telegram user IDs in `ME_TELEGRAM_IDS` and `WIFE_TELEGRAM_IDS`.

The dashboard uses those same two Telegram accounts. The server checks Telegram's signed login, either from the phone Mini App or from the computer "Log in with Telegram" button. A user id sent by the browser on its own is not accepted. After a successful computer login, that browser keeps a signed cookie for 30 days. Log out deletes the cookie. The cookie is signed with the existing bot token, so there is no extra password to store. On a shared computer, use Log out, because anyone using that browser can see the dashboard until the cookie is gone.

For group chats, disable Telegram bot privacy mode only for the intended private household group. Do not add the bot to public groups.

## Data Handling

Expense data is stored in your Google Sheet. Screenshot and voice-note extraction may send image/audio-derived content to OpenAI when `OPENAI_API_KEY` is configured. Bank email is not sent to OpenAI.

Payment method names and card-limit rows are read from your private Google Sheet when needed for payment buttons and card summaries. The bot does not need bank login access and does not pull transactions from banks automatically.

Email logging, when turned on, uses a separate Gmail read-only sign-in. That refresh token is not the spreadsheet key. The bot can read the mailbox. It cannot send mail. The tap from Google is checked before the bot fetches a message. The last four digits are used only to match a card, and they are not stored on the expense row. An unknown shop name may be sent once to Wikipedia. The amount, the card number, the last 4 digits, and the rest of the email are not included, and the mail is not sent to OpenAI.

Avoid sending bank account numbers, card numbers, government IDs, or other unnecessary sensitive information to the bot.

## Supported Version

Security fixes are expected to land on the `main` branch.
