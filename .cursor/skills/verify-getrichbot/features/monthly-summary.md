# Monthly summary

Monthly summary shows household income, expenses, and net for the previous month and the current month, using the raw rows.

## Sub-features

- `month-open` shows the month table from the dashboard navigation.
- `month-september` shows September 2026 Food and Groceries from the seed.
- `month-august` shows August 2026 totals at zero because the seed has no August rows.
- `month-net` shows total expenses and a negative net for September.

## How to get to it (user POV)

- Sign in to the dashboard and choose `Month`.
- There is no separate Telegram command for this table. The bot's monthly summary is written to the Google Sheet tab, which this local server does not use.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open and signed in as Alex.
- No category edit has been saved in this process. `Sample cafe` is still Food.

- **Open Month.** Choose `Month`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Month" --wait-text "Monthly summary"`. The heading is `Monthly summary` and the table title is `From the raw rows`.
- **Read September.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "August 2026" --contains "September 2026" --contains "Food" --contains "$18.50" --contains "Groceries" --contains "$12.40" --contains "Total income" --contains "$0.00" --contains "Total expenses" --contains "$30.90" --contains "Net" --contains "-$30.90"`. September includes both people's seed rows. August has no category amounts.
- **Capture the table.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/month/september.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/month/september.aria.txt`. The artifacts show both month names and `-$30.90`.
- **Sheet entry.** Do not treat a Google Sheet `Monthly Summary` cell as this proof. This server never writes to Google. The on-screen table is the proof.

## Gotchas

- The table is household-wide. `Alex only` on Recent does not filter Month.
- The seed has no income row, so Total income is `$0.00`. A missing income category line is expected. Do not add a fake income row to make a richer table.
- After the recent-transactions recipe changes Sample cafe to Groceries, September Food disappears and Groceries becomes `$30.90`. Launch again before this recipe.
- August category cells are blank. August totals are `$0.00`. Do not fail the blank category cells.
- An unexpected month on the real Google Sheet is a bad raw date, not something this local table can repair.
