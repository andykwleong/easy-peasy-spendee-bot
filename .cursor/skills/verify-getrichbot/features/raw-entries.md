# Raw entries

Raw entries shows the full expense table and the card-only table, including rows logged by the other person.

## Sub-features

- `raw-open` opens the raw table from the dashboard navigation.
- `raw-expenses` lists both seed expense rows with their entry ids.
- `raw-usage` shows an empty Card Usage table.
- `raw-unfiltered` keeps Sam's row visible even when Recent is set to Andy.
- `raw-drag` moves the table sideways from a cell, and leaves the row buttons in place.

## How to get to it (user POV)

- Sign in to the dashboard and choose `Raw entries`.
- There is no Telegram command that renders this table.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open and signed in as Alex.
- Neither seed row has been deleted in this process.

- **Open Raw entries.** Choose `Raw entries`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Raw entries" --wait-text "Raw Expenses"`. The heading is `Raw entries`.
- **Read both tables.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Raw Expenses" --contains "aa1001" --contains "Sample cafe" --contains "bb2002" --contains "Sample market" --contains "Card Usage" --contains "No rows."`. Both expenses are listed. Card Usage is empty.
- **Drag the table.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-drag`. The command prints `drag left=0->180 top=0->0`. The table moved sideways. On this window the page is not taller than the screen, so the up-and-down part stays at 0. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "aa1001" --contains "Sample cafe" --contains "bb2002"`. The rows are still listed.
- **Capture the tables.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/raw/entries.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/raw/entries.aria.txt`. The artifacts show `aa1001`, `bb2002`, and `No rows.` The snapshot still names `Delete Sample cafe`.

## Gotchas

- Raw entries ignores the Recent `Andy`, `FX`, and `Both` chips. Sam's row is supposed to be here.
- The table has no scrollbar. `browser-drag` starts on the Sample cafe cell, so it does not press Delete. The command fails unless the table moves sideways. The same gesture also moves the page up and down when the page is taller than the window. This window prints `top=0->0`.
- The delete control on a raw row is named `Delete Sample cafe` or `Delete Sample market`. This recipe does not click them.
- Card Usage says `No rows.` because the seed has no card-only spend. That empty state is the proof.
- Entry ids are `aa1001` and `bb2002`. A six-character id in Telegram chat is a delete lookup, which this page does not perform.
