# Card summary

Card summary shows the signed-in person's credit cards, including a card with no cap, and can switch to the other person's cards. Tapping a card opens that card's shop, date, and amount directly under it. A second tap closes them.

## Sub-features

- `cards-mine` shows Alex's capped and uncapped cards.
- `cards-other` switches to Sam's card without changing who is signed in.
- `cards-cap` shows Sample Visa at 18% of $100.
- `cards-uncapped` shows Sample Cashback with no cap.
- `cards-transactions` opens Sample cafe under Sample Visa, and Sample market under Sample Mastercard.

## How to get to it (user POV)

- Sign in to the dashboard and choose `Cards` in the section navigation.
- Choose the chip named `Andy` or `FX` to switch person. Andy is Alex's cards. FX is Sam's cards.
- Tap a card to open its purchases under that card. Tap it again to close them.
- In Telegram, `card summary` and `/cards` show only the requester's own cards. This harness does not send those messages.

## Driving it with verify-dashboard

Preconditions:

- A fresh `launch` and a passing `doctor`.
- The browser is open and signed in as Alex, with no category edit yet in this process.
- Sample cafe is still Food, so Sample Visa's spend is still $18.50.

- **Open Cards.** Choose `Cards`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Cards" --wait-text "Card summary"`. The heading is `Card summary`.
- **Alex's cards.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "CAPPED" --contains "Sample Visa" --contains "$18.50 of $100.00" --contains "18%" --contains "UNCAPPED" --contains "Sample Cashback" --contains "No cap" --absent "Sample Mastercard" --absent "Sample cafe"`. Alex's two cards are visible and Sam's card is not. The section labels are shown in capitals. Sample cafe stays hidden until the card is tapped.
- **Capture Alex.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/cards/alex.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/cards/alex.aria.txt`.
- **Open Sample Visa.** Tap `Sample Visa`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Visa" --wait-text "Sample cafe"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --contains "20 Sep 2026" --contains "CAPPED" --contains "UNCAPPED" --contains "Sample Cashback" --absent "Sample market"`. Sample cafe is under Sample Visa. Sample market is not. Both groups stay.
- **Capture the open card.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/cards/visa-open.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/cards/visa-open.aria.txt`.
- **Close Sample Visa.** Tap `Sample Visa` again. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Visa"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample Visa" --contains "CAPPED" --absent "Sample cafe"`.
- **Empty card.** Tap `Sample Cashback`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Cashback" --wait-text "No transactions on this card."`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "No transactions on this card." --contains "Sample Visa" --absent "Sample cafe"`.
- **Sam's cards.** Choose `FX`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "FX" --wait-text "Sample Mastercard"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample Mastercard" --contains "$12.40 of $200.00" --contains "6%" --absent "Sample Visa" --absent "Sample Cashback" --absent "Sample market" --absent "No transactions on this card."`. The header still says `Signed in as Alex`. The list that was open on Sample Cashback is closed.
- **Open Sample Mastercard.** Tap `Sample Mastercard`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Mastercard" --wait-text "Sample market"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample market" --contains "18 Sep 2026" --absent "Sample cafe" --absent "Sample Visa"`.
- **Close Sample Mastercard.** Tap it again. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Sample Mastercard"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample Mastercard" --absent "Sample market"`.
- **Capture Sam.** Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/cards/sam.png` and `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/cards/sam.aria.txt`.
- **Other screens.** Choose `Month`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Month" --wait-text "Monthly summary"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Monthly summary" --absent "No transactions on this card." --absent "Sample cafe"`. Choose `Recent`. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Recent" --wait-text "Sample cafe"`. Sample cafe is still a recent row. Choose `Cards` again. Run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Cards" --wait-text "Card summary"`. Then run `python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample Mastercard" --absent "Sample market" --absent "Sample cafe"`. Andy or FX stays as you left it. Leaving Cards closed the list.
- **Telegram entry.** Do not run `python -m getrichbot.bot`, and do not type `card summary` into Telegram. Those entries stay unverified.

## Gotchas

- The Cards chips are named `Andy` and `FX`. Recent uses the same words, plus `Both`. Only one of those screens is open at a time. A click named `FX` on Cards is Sam's cards.
- Each card is a button whose accessible name is only the card name, such as `Sample Visa`. The dollars stay visible, but they are not part of that name.
- `Sample cafe` stays off Cards until `Sample Visa` is open. Its absence before that tap is the closed state, not a missing row.
- `Sample market` stays off Cards until `Sample Mastercard` is open. Choosing `FX` closes whatever card was open.
- The shop lines sit inside the open card. `CAPPED` stays above the capped cards and `UNCAPPED` stays above the uncapped cards. There is no shop list after the last card while every card is closed.
- The section labels are drawn in capitals, `CAPPED` and `UNCAPPED`. A check for the lowercase words does not see them.
- `18%` and `6%` come from the seed amounts and caps. After a recent-transactions category edit, the card spend is unchanged, but a fresh launch is still required so the recipe starts from the seed.
- Sample Cashback has no Card Limits row. The screen says `No cap`. Do not treat a missing card as a failure.
- A green, yellow, orange, or red band is a colour on the bar. The proof text is the percent and the dollars, not the colour name.
- Telegram `card summary` cannot show the other person's cards. The dashboard can. Do not use one as proof of the other.
