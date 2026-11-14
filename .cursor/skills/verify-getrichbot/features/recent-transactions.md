# Recent transactions

Recent transactions shows the household's latest confirmed rows, can narrow to one person, can filter the rows already on the page, and can correct a row's category from the same list.

## Sub-features

- `recent-locked` shows the locked page before Telegram confirms the visitor.
- `recent-both` shows both people's rows after Both is chosen.
- `recent-alex` shows only rows logged by Alex.
- `recent-sam` shows only rows logged by Sam.
- `recent-dots` shows `Message sent` on Sample cafe and `message fail` on Sample market.
- `recent-fix` opens Fix tagging and saves a new category immediately.
- `recent-search` filters the loaded rows by shop, category, or amount. An empty box shows the loaded list.

## How to get to it (user POV)

- Open the dashboard web page. Before login the page is locked. After login the first screen is Recent.
- In Telegram, send `/dashboard` in a private chat or the household group. The bot replies with a button named `Open dashboard`. This harness does not open that chat.
- On Recent, choose `Andy`, `FX`, or `Both`. The page opens on the signed-in person. Alex opens on Andy. Sam opens on FX. Andy is Alex's rows. FX is Sam's rows.
- Under those chips, type in `Search recent rows`. The placeholder says `Search shop, category, or amount`.
- On a row, choose `Fix tagging`.

## Driving it with verify-dashboard

Preconditions:

- `launch` has printed `ready` and `doctor` has printed `ok`.
- The browser is not open yet.
- The fake sheet still has Food on `Sample cafe` and Groceries on `Sample market`.

- **Open the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-open`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Household money" --contains "This page is locked until Telegram confirms who you are." --absent "Sample cafe"`. The heading is `Household money` and the shop is absent.
- **Capture the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.aria.txt`. Both artifacts show `Household money` and the locked sentence.
- **Sign in as Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-login --as alex --wait-text "Signed in as Alex"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Recent transactions" --contains "Andy" --contains "FX" --contains "Both" --contains "Sample cafe" --contains "Logged by Andy" --contains "Message sent" --absent "Sample market" --absent "Logged by FX" --absent "Logged by Alex" --absent "Logged by Sam"`. The page says `Signed in as Alex` and opens on Andy. Sample cafe is listed. Sample market is not.
- **Show both.** Choose `Both`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Both" --wait-text "Sample market"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --contains "Sample market" --contains "Logged by Andy" --contains "Logged by FX" --contains "Message sent" --contains "message fail" --absent "Logged by Alex" --absent "Logged by Sam"`. Both seed rows are listed.
- **Capture both.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/both.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/both.aria.txt`.
- **Show Alex.** Choose `Andy`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Andy" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --contains "Logged by Andy" --contains "Message sent" --absent "Sample market" --absent "message fail" --absent "Logged by FX" --absent "Logged by Alex" --absent "Logged by Sam"`. Sam's row is gone from this view. The remaining row says `Logged by Andy`.
- **Capture Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/alex.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/alex.aria.txt`.
- **Show Sam.** Choose `FX`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "FX" --wait-text "Sample market"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample market" --contains "Logged by FX" --contains "message fail" --absent "Sample cafe" --absent "Message sent" --absent "Logged by Andy" --absent "Logged by Alex" --absent "Logged by Sam"`. Alex's row is gone from this view. The remaining row says `Logged by FX`.
- **Return to Alex.** Choose `Andy`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Andy" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --absent "Sample market"`.
- **Open Fix tagging.** Choose `Fix tagging`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Fix tagging" --wait-text "Category"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Category" --contains "Amount" --contains "Card" --absent "Channel"`. The category control is present and no channel chooser was invented.
- **Save Groceries.** Choose `Groceries` in the Category control. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-select --label "Category" --value "Groceries" --wait-text "Updated $18.50 to Groceries - 20 September 2026 [aa1001]"`. The row tag reads `Groceries`.
- **Confirm the sheet.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope Alex --path .cursor/skills/verify-getrichbot/artifacts/recent/sheet.json`. The JSON viewer is `Alex`, and the `Sample cafe` row has category `Groceries` and amount `18.50`.
- **Capture the saved row.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/saved.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/saved.aria.txt`.
- **Close Fix tagging.** Choose `Close`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Close" --wait-text "Fix tagging"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Groceries" --contains "Fix tagging" --absent "Amount"`. The panel collapses. The saved category stays.
- **A new purchase arrives.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-arrive --shop "Fresh buns" --logged-by Alex --amount 7.25 --wait-text "Fresh buns"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Fresh buns" --contains "$7.25" --contains "Sample cafe"`. The new row shows without a browser reload.
- **Check who the browser talked to.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-hosts --path .cursor/skills/verify-getrichbot/artifacts/recent/hosts.txt`. Exit code `0`. Hosts are only the local page (`127.0.0.1` with its port) and, if the page script loaded, `telegram.org`.
- **Telegram entry.** Do not run `python -m getrichbot.bot`. That entry stays unverified. The browser path is not a substitute for it.

## Gotchas

- The chips are `Andy`, `FX`, `Both`, and `No category`, in that order, for either signed-in person. `Andy` asks for scope `Alex`. `FX` asks for scope `Sam`. Alex opens on Andy. Sam opens on FX. Sample cafe shows `Logged by Andy` and `Message sent`. Sample market shows `Logged by FX` and `message fail`. The sheet names Alex and Sam stay off the row. A row with no group-note mark shows neither word. This seed has a mark on both rows.
- A search box sits under those four chips. Its accessible name is `Search recent rows`. These steps leave it empty, so both seed rows stay visible. Typing filters shop, category, and amount on the rows already loaded. It does not request the sheet again. Cards, Month, Raw entries, and Eval do not show the box.
- The row's delete control is named `Delete Sample cafe`, not `Delete`. `Delete` is the confirm button inside the dialog.
- Sample Visa has no channel other than All, so Fix tagging does not show a channel chooser. Do not invent one.
- Choosing a category saves immediately. The open row's Save label closes the panel, and it saves an amount only when that amount changed. X closes without saving. After this recipe, Month shows Groceries instead of Food until a fresh launch.
- A save line by itself is not proof. `sheet.json` has to show `Groceries` as well.
- Pressing the verification against the Railway URL, or starting the bot, invalidates the run.
