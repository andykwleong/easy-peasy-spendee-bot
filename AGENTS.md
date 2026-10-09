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
- `Raw Expenses` includes `Payment Owner`, `Payment Method`, and `Payment Channel` after `Description`, followed by `Transaction Type`; valid transaction types are `Expense`, `Income`, and `Fixed`.
- `Logged By` identifies who submitted the transaction. `Payment Owner` identifies whose card or payment account was used. Historical rows without `Payment Owner` fall back to `Logged By`.
- Historical rows with a blank `Payment Method` are valid but must not be included in card tracking.
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
- Category, amount, card, and payment-channel corrections update the Google Sheet immediately, on the dashboard and in Telegram, with no extra confirmation. Do not write on every keystroke: save when a category, card, or channel is picked, or when the amount is finished. Payment owner follows the card. Logged by stays the person who logged the row. Income has no card. Card-only spend has no expense category. If a card has only one non-All channel, keep using it. If it has no channel choice, do not invent one. Other edits, such as a date change, stay confirmation-based. When an edit needs a follow-up date, retain the matched row temporarily for that chat/user until a date or `cancel` is received; a Railway restart clears it.
- `change to Travel`, `change it to Travel`, and `change category to Travel` still update immediately. `change amount to 23.20`, `change card to Sample Visa`, and `change channel to Online` do the same for the sender's latest logged row. Delete still asks for confirmation before anything is removed.
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
- Normal expenses need a visible payment-method selection before they are written to `Raw Expenses`. Income and fixed expenses do not need a payment method.
- Claimable or card-only spend is detected with wording such as `claim`, `claimable`, `reimbursable`, `card only`, or `not expense`. It should ask for payment method, write to `Card Usage`, count toward card summaries/limits, and never write to `Raw Expenses` or `Monthly Summary`.
- `Payment Methods` and `Card Limits` are user-managed Google Sheet tabs. A payment method is identified by its `Owner` and `Payment Method` pair; do not hardcode personal cards in source code.
- Payment buttons show the Telegram sender's active methods first. A bottom partner-card button may show the other configured person's active credit cards. The submitting user remains the only person allowed to choose the payment method.
- Choosing the other person's card must preserve the sender's `Logged By` value and expense category, while storing the selected card's owner in `Payment Owner`.
- `Card Limits` and card summaries count confirmed normal expenses by `Payment Owner`, payment method, category, payment channel, and card cycle. Historical rows without `Payment Owner` use `Logged By`.
- `Payment Channel` is not an expense category. It describes how the card transaction happened, such as `Online`, `PayWave`, or `All`.
- When a selected owner/card has multiple active non-`All` payment channels in `Card Limits`, ask for the channel after payment-method selection and before writing the row. If there is only one active non-`All` channel, use it automatically.
- `Card Usage` includes `Payment Owner` and `Payment Channel`. Its rows are not normal expense-categorised. They count toward the selected owner's total card spend and `All` category card limits, and can count toward channel-specific limits.
- Manual deletion from `Card Usage` should remove that spend from future card summaries because summaries read the current Google Sheet rows.
- A card with no active `Card Limits` row, or with an active row whose `Limit Amount` is blank, is uncapped and must still appear in that owner's card summary with its period spend.
- Use `/refreshpayments` after sheet edits to reload payment config immediately. Otherwise, payment config may remain in a one-minute in-memory cache; it is read only on demand and makes no background Google Sheets calls.
- Payment-selection and screenshot/voice batch state are temporary in-memory state. Railway restarts clear them; the user must send the expense again if a restart happens before its payment button is tapped.
- `card summary` and `/cards` show only the requesting person's active credit cards. The dashboard Cards screen can switch to the other person's credit cards as well. Use green below 60%, yellow from 60% to 79%, orange from 80% to 94%, and red at 95% or above for configured caps. Uncapped cards still show.
- `card summary last month`, `cards last cycle`, and similar previous-period card requests should use the previous card cycle for each card. Billing-cycle cards use their configured reset day; calendar cards use the previous calendar month.
- Specific card-summary period requests such as `card summary July 2026`, `card limits 2026-07`, and `cards 11 Aug` should calculate the card cycle containing that requested month/date.
- Personal history requests such as `expenses on 12 July`, `25th July entry`, `spend on 25th June`, and `expenses between 10-12 July` must only return normal expense rows logged by the requesting Telegram user.
- If a message looks like a personal history request but the date/range cannot be parsed, reply with a clarification message instead of silently ignoring it.
- Category breakdown requests such as `food for june` should show normal expense rows for that category from both configured users and include `Logged By` on each line. `shopping` defaults to the requester's own shopping category; `all shopping` combines both shopping categories.
- Screenshot and voice-note pending entries must remain pending after category/date changes until the user explicitly confirms logging.
- Plain pending replies like `confirm`, `confirmed`, and `confirm all` should target the latest pending batch for that chat/user when one exists; otherwise they should fall back to normal text pending entries.
- Plain `yes` should not confirm screenshot or voice-note pending batches. Reserve `yes` for delete/edit confirmations and similar explicit yes/no prompts.
- `cancel` should discard the visible pending screenshot/voice batch.
- `confirm 1 and 3` should log only the selected visible pending entries and discard the other visible pending entries.
- When pending screenshot or voice-note entries exist, new text/photo/voice expenses should not be logged until the pending batch is confirmed or cancelled.
- Pending confirmation should show `Logging expenses...` before Google Sheets writes begin.
- Screenshot handling should show only one progress message before extraction results: `Reading screenshot...`.
- If a duplicate is found during pending batch confirmation, stop at the first duplicate and wait for `confirm` or `cancel`. Do not continue through the batch and do not remove the pending item until it is actually logged or cancelled.
- Duplicate checks should include a one-minute recently logged in-memory window so immediately repeated confirmations are caught even before Google Sheets read-back reflects the append.
- A bare 6-character entry ID should be treated as a delete lookup, not parsed as an expense amount.
- Email logging stays off unless `EMAIL_LOGGING_ENABLED` is on and the Gmail OAuth and Pub/Sub topic settings are present. Missing settings must not crash startup. The Gmail refresh token is separate from the Sheets service account. Do not send mail to OpenAI. Do not commit the mailbox address, refresh token, or real card digits.
- Every mail in the agent inbox is read. The subject is not a gate. If the body has one amount, one date, and one card, write the row. The word "on" is not required. A date anywhere in the body may be `09/10/26`, `09/10/2026`, `09 Oct`, `09 OCT`, `05 Oct` with a clock time that is not part of the date, or `08/OCT/2026`. A month with no year uses the year from another date in the same mail, otherwise the current year. A label such as `Transaction date` or `Date & Time` may be on the same line or on the line above the value. A clock time may sit between the date and the shop without the word "at" before the time, including `on 05/10/26 14:22 at ...` and `on 05/10/26 2:22PM at ...`. A missing clock time does not block the row. Amounts `SGD231.66` and `SGD 231.66` count, including after `Transaction amount` or `Amount:`. If several amounts or last-4 values appear, use the ones in that first purchase sentence. Last 4 may be `card ending 1111`, `ending 1111`, `A/C ending 1111`, `****1111`, or `XXXX-XXXX-XXXX-1111`. The card is the last group of 4 digits. A longer unbroken number is not a card. `(UEN ending ABCD)` is not a card. The shop is the text after `To`, `Merchant`, `Shop`, `Transaction details`, or `Description`. The label may use a colon, and the value may be on the next line. Drop a trailing UEN parenthesis, so `To: SAMPLE MERCHANT (UEN ending ABCD)` is shop `SAMPLE MERCHANT`. The existing "at the shop" line still works. A missing shop does not block the row. If the amount, the date, or the card is missing, name that missing piece for every bank and do not write. Do not use the "not one clear purchase" sentence for that case. Match the card by the last 4 digits to the Payment Methods `Last 4` column only. Never invent a category name. If the body contains a category name already on the sheet, use that name, including an income-tax mail whose body names the income-tax category. Otherwise use the sheet keyword matcher, and also match when the category name is contained in the shop text. `fp*Food Panda` must land on the food category when the food keyword or the category name `Food` is contained in the shop. If the shop is not already on Category Keywords, call the Exa search API once with the shop name only. Env var `EXA_API_KEY`. If the key is missing, or Exa fails, still log the row and leave the category blank. Do not crash. Then ask the configured model (`gpt-5.4-mini` unless `OPENAI_MODEL` is changed) once: shop name, Exa's short description, and the existing category names. It must pick one of those names or say it cannot. Do not send the amount, the card, the last 4, or the rest of the email. Do not invent a category. Save the shop and chosen category on Category Keywords so the next mail does not search again. If that write fails, the purchase is still logged. If no category is chosen, log the row with a blank category. Telegram text is two lines: the logged line, then `Category is missing. Reply to this message with the category.` A category typed with no Telegram reply attaches to the bot message immediately before it. A reply to an older bot message attaches to that older row only. The dashboard category picker updates the same row and saves the shop keyword. The link is the Telegram message id stored on the row. A restart does not forget it. Do not add a manual type list of words such as restaurant or sushi.
- A refund subject `Your transaction has been refunded`, incoming PayNow subject `UOB-PayNow transfer received`, and dividend or interest mail are written immediately as the sheet category named exactly `Income - misc`. Do not reverse or edit the original purchase. Do not ask for approval. Income is household income and has no card. The card total still includes the original purchase because the refund is income, not a reversal. Own-account PayNow transfers are kept out by the Gmail filter, not by the bot. An outgoing payment from an account to a merchant, with an amount and a date, is an expense. Do not log that as `Income - misc`.
- Do not invent `Income - A` or `Income - fx` for email. If `Income - misc` is missing from the configured category list, ask in Telegram and do not write.
- Statement subject `Your eStatement/eAdvice is ready for viewing` stays quiet, including a statement with no amount. Do not guess `Income - A` or `Income - fx`. Mail that is missing the amount, the date, or the card names each missing piece and does not write. Salary without those three facts still asks and does not write.
- Email channel rules match card channels: Foodpanda-style delivery is Online, a restaurant is PayWave, one non-All channel is used automatically, and both channels with neither shop type still log the row with a blank channel. Telegram shows All for that blank channel. The dashboard can set the channel afterwards.
- `pause email` calls Gmail users.stop. `resume email` starts the watch again. First start and mail that piled up while paused ask before logging. Replies are `log email backlog` and `skip email backlog`.
- Bot State stores `gmail_history_id`, pause, backlog ids, `last_bot_message_id`, and `gmail_message_id:<id>` so a restart does not log the same mail twice and a category reply can find the previous bot message. A pending channel question is also stored there, without the mail body or last 4.
- A daily job renews the Gmail watch once a day because Google stops tapping after 7 days. It only renews the tap. It does not read the mailbox when there is nothing new. Skip it when email logging is off or paused.

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
