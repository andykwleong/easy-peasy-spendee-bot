# Card summary

Card summary shows the signed-in person's credit cards, including a card with no cap, and can switch to the other person's cards.

## Sub-features

- `cards-mine` shows Alex's capped and uncapped cards.
- `cards-other` switches to Sam's card without changing who is signed in.
- `cards-cap` shows Sample Visa at 18% of $100.
- `cards-uncapped` shows Sample Cashback with no cap.

## How to get to it (user POV)

- Sign in to the dashboard and choose `Cards` in the section navigation.
- Choose the chip named `Alex` or `Sam` to switch person.
- In Telegram, `card summary` and `/cards` show only the requester's own cards. This harness does not send those messages.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open and signed in as Alex, with no category edit yet in this process.
- Sample cafe is still Food, so Sample Visa's spend is still $18.50.

- **Open Cards.** Choose `Cards`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Cards" --wait-text "Card summary"`. The heading is `Card summary`.
- **Alex's cards.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Capped" --contains "Sample Visa" --contains "$18.50 of $100.00" --contains "18%" --contains "Uncapped" --contains "Sample Cashback" --contains "No cap" --absent "Sample Mastercard"`. Alex's two cards are visible and Sam's card is not.
- **Capture Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/cards/alex.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/cards/alex.aria.txt`.
- **Sam's cards.** Choose `Sam`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sam" --wait-text "Sample Mastercard"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample Mastercard" --contains "$12.40 of $200.00" --contains "6%" --absent "Sample Visa" --absent "Sample Cashback"`. The header still says `Signed in as Alex`.
- **Capture Sam.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/cards/sam.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/cards/sam.aria.txt`.
- **Telegram entry.** Do not run `python -m getrichbot.bot`, and do not type `card summary` into Telegram. Those entries stay unverified.

## Gotchas

- The Cards chips are named `Alex` and `Sam`. Recent uses `Alex only`, `Sam only`, and `Both`. They are different controls. A click named `Sam` is the Cards chip.
- `18%` and `6%` come from the seed amounts and caps. After a recent-transactions category edit, the card spend is unchanged, but a fresh launch is still required so the recipe starts from the seed.
- Sample Cashback has no Card Limits row. The screen says `No cap`. Do not treat a missing card as a failure.
- A green, yellow, orange, or red band is a colour on the bar. The proof text is the percent and the dollars, not the colour name.
- Telegram `card summary` cannot show the other person's cards. The dashboard can. Do not use one as proof of the other.
