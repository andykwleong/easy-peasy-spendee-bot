# Recent transactions

Recent transactions shows the signed-in person's latest confirmed rows, can include the other person, can show only rows that still have no category, and can correct a row's category from the same list.

## Sub-features

- `recent-locked` shows the locked page before Telegram confirms the visitor.
- `recent-mine` shows only the signed-in person's rows after login.
- `recent-both` adds the other person's rows without leaving Recent.
- `recent-missing` shows only rows whose category is still blank. A blank expense row is highlighted and tagged `No category`.
- `recent-fix` opens Fix tagging and saves a new category immediately. While that panel is open the same control reads `Save`.

## How to get to it (user POV)

- Open the dashboard web page. Before login the page is locked. After login the first screen is Recent.
- In Telegram, send `/dashboard` in a private chat or the household group. The bot replies with a button named `Open dashboard`. This harness does not open that chat.
- On Recent, choose `Alex only`, `Both`, or `No category`.
- On a row, choose `Fix tagging`. While that panel is open the same control reads `Save`.

## Driving it with verify-dashboard

Preconditions:

- `launch` has printed `ready` and `doctor` has printed `ok`.
- The browser is not open yet.
- The fake sheet still has Food on `Sample cafe` and Groceries on `Sample market`.

- **Open the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-open`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Household money" --contains "This page is locked until Telegram confirms who you are." --absent "Sample cafe"`. The heading is `Household money` and the shop is absent.
- **Capture the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.aria.txt`. Both artifacts show `Household money` and the locked sentence.
- **Sign in as Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-login --as alex --wait-text "Signed in as Alex"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Recent transactions" --contains "No category" --contains "Sample cafe" --contains "$18.50" --contains "Food" --absent "Sample market" --absent "Sample stall"`. The page says `Signed in as Alex` and lists only Alex's row. The `No category` chip is on the page.
- **Capture one person.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/mine.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/mine.aria.txt`.
- **Show both people.** Choose `Both`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Both" --wait-text "Sample market"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --contains "Sample market" --contains "Sample stall" --contains "Logged by Sam"`. Both shops are visible, including the blank category row.
- **Capture both.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/both.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/both.aria.txt`.
- **Return to Alex.** Choose `Alex only`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Alex only" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --absent "Sample market" --absent "Sample stall"`. Sam's rows are gone from this view.
- **No category, Alex only.** Choose `No category`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "No category" --wait-text "No rows are missing a category."`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "No rows are missing a category." --absent "Sample cafe" --absent "Sample stall"`. Alex has no blank category, so the list is empty.
- **Capture the empty filter.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/missing-empty.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/missing-empty.aria.txt`.
- **No category, both people.** Choose `Both`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Both" --wait-text "Sample stall"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample stall" --contains "No category" --contains "$4.00" --absent "Sample cafe" --absent "Sample market"`. Only the blank row remains, and its tag reads `No category`.
- **Capture the blank row.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/missing.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/missing.aria.txt`.
- **Clear the filter and return to Alex.** Choose `No category` again. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "No category" --wait-text "Sample cafe"`. Then choose `Alex only`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Alex only" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --absent "Sample stall"`.
- **Open Fix tagging.** Choose `Fix tagging`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Fix tagging" --wait-text "Category"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Save" --contains "Category" --contains "Amount" --contains "Card" --absent "Channel"`. The open control reads `Save`. The category control is present and no channel chooser was invented.
- **Save Groceries.** Choose `Groceries` in the Category control. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-select --label "Category" --value "Groceries" --wait-text "Updated $18.50 to Groceries - 20 September 2026 [aa1001]"`. The row tag reads `Groceries`.
- **Confirm the sheet.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope mine --path .cursor/skills/verify-getrichbot/artifacts/recent/sheet.json`. The JSON viewer is `Alex`, and the `Sample cafe` row has category `Groceries` and amount `18.50`.
- **Capture the saved row.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/saved.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/saved.aria.txt`.
- **Check who the browser talked to.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-hosts --path .cursor/skills/verify-getrichbot/artifacts/recent/hosts.txt`. Exit code `0`. Hosts are only the local page (`127.0.0.1` with its port) and, if the page script loaded, `telegram.org`.
- **Telegram entry.** Do not run `python -m getrichbot.bot`. That entry stays unverified. The browser path is not a substitute for it.

## Gotchas

- `Alex only` is the visible label. The scope value `mine` is not a button name.
- The row's delete control is named `Delete Sample cafe`, not `Delete`. `Delete` is the confirm button inside the dialog.
- Sample Visa has no channel other than All, so Fix tagging does not show a channel chooser. Do not invent one.
- Choosing a category saves immediately. While Fix tagging is open, that same control reads `Save`. `Save` commits a finished amount. It does not ask again before the category change. After this recipe, Month shows Groceries instead of Food until a fresh launch.
- `Sample stall` is Sam's row and its category is blank. It stays off `Alex only`. On `Alex only`, `No category` says `No rows are missing a category.`
- A card-only row is not highlighted and is not tagged `No category`. This seed has no card-only row.
- A save line by itself is not proof. `sheet.json` has to show `Groceries` as well.
- Pressing the verification against the Railway URL, or starting the bot, invalidates the run.
