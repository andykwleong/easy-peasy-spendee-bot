# Eval

Eval shows three scores for the open month: the share of mail that was logged, the share of email purchases that kept the category they were logged with, and the share of group notes Telegram accepted.

## Sub-features

- `eval-open` opens Eval from the section navigation, after Raw entries.
- `eval-email` shows 67% of the September mail logged.
- `eval-category` shows 50% of the email purchases unchanged, and names the one that changed.
- `eval-group` shows 50% of the group notes sent, and names the one Telegram did not accept.
- `eval-period` shows the same three scores on All time.

## How to get to it (user POV)

- Sign in to the dashboard and choose `Eval`.
- There is no Telegram command for this tab.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open and signed in as Alex.
- No category edit has been saved in this process.

- **Open Eval.** Choose `Eval`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Eval" --wait-text "Email successfully logged"`. The heading is `Eval`.
- **Read September.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Email successfully logged" --contains "67%" --contains "logged" --contains "2 logged" --contains "1 not logged" --contains "Quiet note" --contains "No amount" --contains "Categories successfully matched" --contains "50%" --contains "unchanged after logging" --contains "1 left as logged" --contains "1 changed later" --contains "Sample market: Groceries, then Food" --contains "Telegram message successfully sent" --contains "sent" --contains "1 sent" --contains "1 not sent" --contains "Sample market · $12.40" --contains "September 2026" --contains "All time" --absent "Reading that mail" --absent "Telegram did not accept the group note"`. The open month is September 2026. The mail body and the failed group note are closed.
- **Capture the scores.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/eval/september.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/eval/september.aria.txt`.
- **All time.** Choose `All time`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "All time" --wait-text "67%"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "67%" --contains "50%" --contains "2 logged" --contains "1 changed later" --contains "1 not sent"`. The same three rows are in the only month, so the scores stay.
- **Mail body.** Do not tap the Quiet note rectangle. This server has no mailbox. The mail text stays hidden until that rectangle is tapped, and tapping it again would close it.

## Gotchas

- The three scores come from the verification Eval Log and the two group-note marks. They are not the household sheet. Cleanup drops them.
- 67% is 2 logged mails out of 3. 50% unchanged is 1 of 2 email purchases. 50% sent is 1 of 2 group notes.
- The mail that was not logged is `Quiet note`, reason `No amount`. The purchase that changed is Sample market, from Groceries to Food. The group note that failed is Sample market.
- Tapping a not-logged mail rectangle asks this server to read one Gmail message. There is no mailbox here, so that tap cannot show a message. The scores do not depend on that read. The body is not shown until the tap, and it is not saved.
- Saving a new category on Recent can change the category score. Launch again before this recipe if a category was saved.
