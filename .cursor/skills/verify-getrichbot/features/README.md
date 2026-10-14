# Household dashboard verification map

This directory is the maintained source for verifying the user-facing household dashboard. Read this index before driving the app, then use the matching feature file as the recipe.

Telegram chat is a second surface. This map does not drive it. The live bot already runs on Railway, and a second `python -m getrichbot.bot` process would conflict with it.

## Baseline preconditions

- Launch the dashboard with `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py launch`.
- The page is `http://127.0.0.1:41731` unless `GETRICHBOT_VERIFY_PORT` set another port. Read the `url=` line from launch. Never substitute a public URL.
- The fake sheet starts with `Sample cafe` (`aa1001`, Alex, Food, $18.50, Sample Visa, 20 Sep 2026) and `Sample market` (`bb2002`, Sam, Groceries, $12.40, Sample Mastercard, 18 Sep 2026).
- Alex's cards are Sample Visa (cap $100, shows 18%) and Sample Cashback (no cap). Sam's card is Sample Mastercard (cap $200, shows 6%).
- The open month is September 2026 because the server's today is 26 Sep 2026.
- Run `doctor` and require the first line `ok`.
- Never drive an instance that this verification run did not start.

## Driving conventions

- Start every recipe from a fresh launch unless that feature's preconditions say the browser is already signed in.
- Prefer the role and accessible name in the feature file. Do not click by pixel or by the order of buttons.
- Treat every command as literal. Keep quoted names unchanged.
- Run browser actions through `verify_dashboard.py`. There is no Telegram command in this harness.
- A category change or a confirmed delete stays in the server's memory. Clean up and launch again before the next feature.
- Do not remove proof artifacts during cleanup.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- UI proof includes an ARIA snapshot and a screenshot that shows `Household money`.
- A changed row also needs `sheet-show` for that scope. The JSON must contain the same shop, amount, and category the screen shows.
- Record the feature id in the artifact directory name.
- The Telegram entry is unreachable here. Report the refused command `python -m getrichbot.bot` and the reason: Railway already runs the only live bot. Do not report the browser path as proof of the Telegram entry.
- `browser-hosts` must succeed. `telegram.org` is allowed. Google, Railway, OpenAI, and Exa hosts are not.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with verify-dashboard` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Recent transactions](./recent-transactions.md) covers the locked page, signing in, one person versus both, and fixing a category.
- [Card summary](./card-summary.md) covers the signed-in person's cards, the other person's cards, a cap, and a card with no cap.
- [Monthly summary](./monthly-summary.md) covers the household month table built from the raw rows.
- [Raw entries](./raw-entries.md) covers the full raw expense table and the empty card-only table.
- [Delete a row](./delete-row.md) covers the confirm dialog, cancel, and a confirmed delete.
