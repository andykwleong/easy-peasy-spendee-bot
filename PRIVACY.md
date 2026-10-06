# Privacy

GetRichBot is a private household finance assistant. It is designed for a small trusted Telegram chat, usually between two spouses, and stores confirmed expense records in a Google Sheet you control.

## Data The Bot Processes

Depending on how you use it, the bot may process:

- Telegram user IDs and chat IDs
- Expense text you send in Telegram
- Expense amounts, dates, categories, and descriptions
- Your private category names and keyword rules
- Payment method names, card owners, card cycle setup, and card limit rows
- Screenshots you upload for expense extraction
- Voice notes you upload for transcription and extraction
- Fixed expense categories and default amounts from your Google Sheet
- Bank email that you forward into a mailbox the bot is allowed to read, when email logging is turned on

## Where Data Goes

Confirmed expense entries are written to your configured Google Sheet.

The private dashboard reads that same Google Sheet and shows it in the browser. Fix tagging writes a category, amount, card, or channel correction back to the same row. On a computer, after you log in with Telegram, that browser keeps a login cookie for 30 days. Log out deletes the cookie. The cookie is not written into the Google Sheet.

When email logging is turned on, the bot reads messages from a mailbox you choose. It uses a Gmail read-only sign-in that is separate from the Google Sheets key. It writes a purchase or an income row to the sheet. It does not send the mail to OpenAI. The last four digits of a card are used only to match a card name, and they are not written on the expense row. If a shop is not already in your category keywords, the shop name alone is sent once to Wikipedia so it can be matched to a category you already have. The amount, the card, the last 4 digits, and the rest of the email are not sent. That lookup is not repeated on a timer.

Payment method and card limit setup is read from your configured Google Sheet to show payment buttons, card summaries, and card-limit usage. Category breakdown and personal history requests read confirmed rows from `Raw Expenses` and reply in Telegram.

When `OPENAI_API_KEY` is configured:

- Screenshot extraction may send the image or prepared image content to OpenAI.
- Voice-note transcription may send audio content to OpenAI.
- Natural-language edit, delete, and question handling may send the relevant text and nearby expense context to OpenAI.

Telegram processes messages and media according to Telegram's own service terms and privacy policy.

Railway hosts the bot process when deployed. Railway environment variables should hold production secrets such as Telegram tokens, OpenAI keys, and Google service account JSON.

Your private category configuration usually lives in the Google Sheet `Categories` and `Category Keywords` tabs. If you keep `CATEGORIES_JSON` in Railway as a fallback, that Railway variable may also contain private category names and keywords. Do not commit your real `categories.json` file if you use JSON fallback and your category names or keywords are personal.

## What The Bot Does Not Do

- It does not publish your expenses publicly.
- It does not need bank login access.
- It does not scrape your bank account automatically.
- It does not send bank email to OpenAI.
- It does not store or require full card numbers.
- It does not intentionally store screenshots or voice notes after processing.
- It does not make background OpenAI calls unless handling a message, media upload, natural-language action, or scheduled summary/reminder logic that needs bot processing.

## Your Responsibilities

- Keep the bot in a private Telegram group.
- Configure only trusted Telegram user IDs.
- Do not commit `.env`, service account JSON files, or real API keys.
- Avoid sending unnecessary sensitive details such as full card numbers, bank account numbers, or government IDs.
- Review your Google Sheet sharing settings before making any repository public.

## Removing Data

Expense rows can be deleted from Google Sheets directly, or through the bot's delete flow when supported.

If you want to start over, clear the relevant rows in `Raw Expenses`, `Monthly Summary`, and `Bot State` while keeping the header rows.
