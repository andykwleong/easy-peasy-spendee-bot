# Privacy

GetRichBot is a private household finance assistant. It is designed for a small trusted Telegram chat, usually between two spouses, and stores confirmed expense records in a Google Sheet you control.

## Data The Bot Processes

Depending on how you use it, the bot may process:

- Telegram user IDs and chat IDs
- Expense text you send in Telegram
- Expense amounts, dates, categories, and descriptions
- Your private category names and keyword rules
- Screenshots you upload for expense extraction
- Voice notes you upload for transcription and extraction
- Fixed expense categories and default amounts from your Google Sheet

## Where Data Goes

Confirmed expense entries are written to your configured Google Sheet.

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
