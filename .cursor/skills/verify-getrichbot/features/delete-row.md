# Delete a row

Delete a row asks for confirmation, can be cancelled, and removes the row only after the confirm button is chosen.

## Sub-features

- `delete-ask` opens a dialog named `Delete this row?` for one shop.
- `delete-cancel` closes the dialog and leaves the row in place.
- `delete-confirm` removes that row from Recent and from the sheet read.

## How to get to it (user POV)

- On Recent, choose the delete control on one row. Its accessible name includes the shop, for example `Delete Sample cafe`.
- In the dialog, choose `Cancel` or `Delete`.
- In Telegram, replying `delete` to a logged message is a different path. This harness does not send that reply.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open, signed in as Alex, and Recent is showing only Alex's row.
- `Sample cafe` is still present. A previous confirmed delete in this process makes this recipe fail. Launch again.

- **Ask.** Choose `Delete Sample cafe`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Delete Sample cafe" --wait-text "Delete this row?"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Delete this row?" --contains "Sample cafe" --contains "$18.50"`. The dialog names the shop and the amount.
- **Capture the question.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/delete/ask.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/delete/ask.aria.txt`. The snapshot includes a dialog named `Delete this row?`.
- **Cancel.** Choose `Cancel`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Cancel" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --absent "Delete this row?"`. The row is still listed.
- **Confirm the sheet still has it.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope mine --path .cursor/skills/verify-getrichbot/artifacts/delete/after-cancel.json`. The JSON still contains `aa1001` and `Sample cafe`.
- **Ask again and confirm.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Delete Sample cafe" --wait-text "Delete this row?"` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Delete" --wait-text "No confirmed rows for this view."`. Alex's Recent list is empty.
- **Confirm the sheet lost it.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope both --path .cursor/skills/verify-getrichbot/artifacts/delete/after-confirm.json`. The JSON contains `Sample market` and does not contain `aa1001`.
- **Capture the empty list.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/delete/gone.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/delete/gone.aria.txt`.
- **Telegram entry.** Do not run `python -m getrichbot.bot`. A Telegram `delete` reply stays unverified.

## Gotchas

- The row control is `Delete Sample cafe`. The dialog's confirm control is `Delete`. Clicking the wrong one either does nothing useful or deletes before the dialog is checked.
- Cancel must be proved with `after-cancel.json` still containing `aa1001`. A closed dialog is not enough.
- Confirm deletes only inside this server's memory. The household Google Sheet is not changed. Launch again to restore `Sample cafe`.
- `sheet-show --scope mine` after confirm can be an empty recent list. Use `--scope both` to see that Sam's row survived.
- Do not confirm a delete on a server you did not launch.
