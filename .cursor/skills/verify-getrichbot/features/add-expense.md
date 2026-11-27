# Add an expense

The signed-in person types the same expense text they would send in Telegram. The page hands that text to the existing message handler. A payment button in the reply uses that same handler, and the new row then shows on Recent.

## Sub-features

- `add-box` shows the expense box after sign-in and hides it on the locked page.
- `add-payment` asks for a payment method and does not add a row until a button is chosen.
- `add-row` writes the row through that button and shows it for the signed-in person.

## How to get to it (user POV)

- Open the dashboard web page and sign in. The box sits above Recent on every signed-in page. Its label is `Add an expense`. The text box is `Expense text`. The button is `Send expense`.
- Type the expense, choose Send expense, then choose the payment button the reply shows.
- In Telegram, the same words still go to the bot. This harness does not open that chat.

## Driving it with verify-dashboard

Preconditions:

- `launch` has printed `ready` and `doctor` has printed `ok`.
- The browser is not open yet.
- The fake sheet still has `Sample cafe` and `Sample market`.

- **Open the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-open`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Household money" --contains "This page is locked until Telegram confirms who you are." --absent "Add an expense" --absent "Sample cafe"`. The expense box is absent before sign-in.
- **Capture the locked page.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/add-expense/locked.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/add-expense/locked.aria.txt`.
- **Sign in as Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-login --as alex --wait-text "Add an expense"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Add an expense" --contains "Signed in as Alex" --contains "Sample cafe"`.
- **Capture the box.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/add-expense/box.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/add-expense/box.aria.txt`.
- **Type the expense.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-fill --label "Expense text" --value "lunch 23.40"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Send expense" --wait-text "Which payment method?"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Which payment method?" --contains "Sample Visa" --contains "$23.40" --absent "Logged $23.40"`.
- **Read the sheet before the button.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope Alex --path .cursor/skills/verify-getrichbot/artifacts/add-expense/before.json`. The file has Sample cafe and does not have lunch.
- **Capture the payment question.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/add-expense/payment.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/add-expense/payment.aria.txt`.
- **Choose Sample Visa.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Visa" --wait-text "Logged $23.40"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Logged $23.40" --contains "lunch" --contains "Logged by Andy" --contains "Message sent"`. The payment question stays on the page. The saved line is what the household group would receive.
- **Read the sheet after the button.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope Alex --path .cursor/skills/verify-getrichbot/artifacts/add-expense/after.json`. The file has lunch, Food, 23.40, and Sample Visa. The lunch row's `message_status` is `Message sent`. The before file has no lunch row.
- **Capture the logged row.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/add-expense/logged.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/add-expense/logged.aria.txt`.
- **Record hosts.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-hosts --path .cursor/skills/verify-getrichbot/artifacts/add-expense/hosts.txt`.

## Gotchas

- The payment question contains the word lunch. A row is logged only after Sample Visa is chosen. Check the sheet file from before the click as well as the page.
- Sample cafe already uses Sample Visa. The new row is the one whose shop is lunch and whose amount is $23.40.
- The server's today for Month is 26 Sep 2026. The typed expense is dated the real today, so it shows on Recent and may be outside the September month table.
- Do not run `python -m getrichbot.bot`. That command is refused because Railway already runs the only live bot.
- This server records the household confirmation in memory and does not call Telegram. The lunch row's `message_status` is how that record shows on the page.
