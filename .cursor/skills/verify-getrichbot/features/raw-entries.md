# Raw entries

Raw entries shows the full expense table and the card-only table, including rows logged by the other person.

## Sub-features

- `raw-open` opens the raw table from the dashboard navigation.
- `raw-expenses` lists both seed expense rows with their entry ids.
- `raw-usage` shows an empty Card Usage table.
- `raw-unfiltered` keeps Sam's row visible even when Recent is set to Alex only.

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
- **Capture the tables.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/raw/entries.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/raw/entries.aria.txt`. The artifacts show `aa1001`, `bb2002`, and `No rows.`

## Gotchas

- Raw entries ignores the Recent `Alex only`, `Sam only`, and `Both` chips. Sam's row is supposed to be here.
- The delete control on a raw row is named `Delete Sample cafe` or `Delete Sample market`. This recipe does not click them.
- Card Usage says `No rows.` because the seed has no card-only spend. That empty state is the proof.
- Entry ids are `aa1001` and `bb2002`. A six-character id in Telegram chat is a delete lookup, which this page does not perform.
