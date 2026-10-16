---
name: verify-getrichbot
description: "Drive the GetRichBot household web dashboard on a local fake-sheet server. Use to prove Recent, Cards, Month, Raw entries, or delete behavior without starting the live Telegram bot or opening the Railway site."
---

# Verify GetRichBot

The primary surface is the household web dashboard (`Household money`). Telegram chat is a second surface. This skill drives only the dashboard.

Do not run `python -m getrichbot.bot`. Railway is the only live bot. A second process would steal the same Telegram connection. Do not open the production dashboard URL. The helper builds `http://127.0.0.1:<port>` and refuses any other host.

The sheet behind this dashboard is an in-memory stand-in, built the same way `tests/test_dashboard_http.py` builds `FakeSheets`. It is not the household Google Sheet. Login uses the real `/api/session/widget` check, signed with a local verification token that is not a Telegram bot token. The page still loads `https://telegram.org/js/telegram-web-app.js` because that script tag is in the real dashboard HTML. No bot token and no sheet rows are sent with that request. The Telegram login widget is not mounted, because this server has no bot username.

## Launch

From the repo root:

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py launch
```

Ready means a later `doctor` command prints `ok`, and `GET /` returns the household page with the words `Household money`. The page is locked: `GET /api/dashboard` returns 401 and the HTML does not contain `Sample cafe`.

What launch does:

- Listens on `127.0.0.1` only. The default port is `41731`. Override with `GETRICHBOT_VERIFY_PORT`. It does not read `PORT`, so it will not attach to a Railway-style port by accident.
- Seeds three confirmed rows and three sample cards. Today is fixed at 26 Sep 2026, so the open month is September 2026.
  - `aa1001`: Alex, 20 Sep 2026, Food, Sample cafe, $18.50, Sample Visa.
  - `bb2002`: Sam, 18 Sep 2026, Groceries, Sample market, $12.40, Sample Mastercard.
  - `cc3003`: Sam, 21 Sep 2026, no category, Sample stall, $4.00, Cash. Cash is not a credit card, so Cards does not list it. Month leaves a blank category out of the totals.
  - Sample Visa (Alex) has a $100 calendar cap. Sample Cashback (Alex) has no cap. Sample Mastercard (Sam) has a $200 calendar cap.
- Uses the public example categories (Food, Groceries, and the other names in the repo's example list). It does not read `.env`, `categories.json`, or a Google Sheet.
- If `python-dotenv` is missing, launch creates `.venv` and installs `python-dotenv==1.0.1`, then restarts itself with that interpreter. `.venv` is gitignored. It does not install the Telegram or Google libraries and it does not start the bot. Creating `.venv` needs the system package that provides `python3 -m venv` (on Ubuntu, `python3.12-venv`). If that package is missing, launch stops with the venv error. Install the package and run launch again.
- Writes the process record to `/tmp/getrichbot-verify/instance.json`. Override the directory with `GETRICHBOT_VERIFY_STATE`. One verification server at a time. A second launch refuses while the first is still running.

The seed has no income row. Total income on Month is `$0.00` until a run adds one.

## Doctor

Run this first whenever the page looks wrong:

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py doctor
```

Doctor is read-only. It does not log in and it does not change a row. Exit code `0` and a first line `ok` mean the instance is worth driving. It checks all of these:

- The recorded pid is alive and its command is this helper's `serve` command, not `python -m getrichbot.bot`.
- That pid owns the listen port, and the listen address is `127.0.0.1`.
- The URL host is `127.0.0.1`.
- `GET /` is the household page and does not contain sheet rows.
- `GET /api/dashboard` is 401.
- `GET /api/config` has an empty bot username.
- The server process has no network connection except this computer.
- The instance record says `sheets=fake`.

Anything else prints `not ok` and exits `1`. Do not drive it.

## Drive

Read `features/README.md`, then the feature file. Run the commands there literally.

Browser commands talk to a Chrome window this helper started. They click by role and accessible name. A button's accessible name is its `aria-label` when it has one (`Delete Sample cafe`), otherwise its visible text (`Both`, `Fix tagging`, `Cards`).

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-open
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-login --as alex --wait-text "Signed in as Alex"
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-click --role button --name "Both" --wait-text "Sample market"
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-select --label "Category" --value "Groceries" --wait-text "Updated $18.50 to Groceries"
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-expect --contains "Sample cafe" --absent "Sample market"
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py sheet-show --scope both --path .cursor/skills/verify-getrichbot/artifacts/recent/sheet.json
```

`browser-login` runs the same callback the Telegram login widget calls (`onTelegramAuth`). The browser then POSTs that signed payload to the local `/api/session/widget` endpoint. The session cookie is HttpOnly. Later `sheet-show` reads `/api/dashboard` from the page so the cookie is included. That is a second view of the same rows, not a hidden test route.

`browser-click` fails if zero or more than one control has that role and name. It does not guess.

There is no command that opens Telegram. The refused command is `python -m getrichbot.bot`.

## Evidence

Proof artifacts go under `.cursor/skills/verify-getrichbot/artifacts/<feature>/`. The helper rejects a path outside that directory. Cleanup does not delete it. The directory is gitignored.

For a UI proof, capture the screen before the action and after it:

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-screenshot --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.png
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-snapshot --aria --path .cursor/skills/verify-getrichbot/artifacts/recent/locked.aria.txt
```

A row change is proved only when the page shows the new value and `sheet-show` returns that same value. A screenshot of the last screen, with no picture of the action that produced it, is not enough.

After the browser steps, record the hosts the page requested:

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py browser-hosts --path .cursor/skills/verify-getrichbot/artifacts/recent/hosts.txt
```

Exit code `0` means every host is local (`127.0.0.1` or `localhost`, with or without a port such as `127.0.0.1:41731`) or `telegram.org`. `telegram.org` is the public web-app script in the real HTML. A host such as `sheets.googleapis.com`, `railway.app`, `openai.com`, or `api.exa.ai` fails the command. Do not treat that run as proof.

These stands-in are not the live household:

- The sheet is memory inside the verification process. A Google Sheet write is not what a passing run observed.
- Login is signed locally. A real Telegram account was not used.
- The bot process was not started, so a Telegram chat entry was not exercised.
- Categories are the public example list, even if this machine has a private `categories.json`.

A pass here does not by itself prove the Railway site.

## Cleanup

```bash
python3 .cursor/skills/verify-getrichbot/scripts/verify_dashboard.py cleanup
```

Cleanup stops only the server and Chrome whose pids are in `/tmp/getrichbot-verify/instance.json`, and only when their command lines still match this helper. It does not kill by process name. It deletes `/tmp/getrichbot-verify` (the process record, the server log, and the Chrome profile). It leaves `.cursor/skills/verify-getrichbot/artifacts/` in place.

Run cleanup after a failed launch or a failed drive too, so a broken attempt does not keep the port.

Start a fresh launch for each feature. Category edits and deletes stay in the server's memory until cleanup.

## Helpers

The only helper is `.cursor/skills/verify-getrichbot/scripts/verify_dashboard.py`. Run it with `python3` from the repo root. Commands: `launch`, `doctor`, `browser-open`, `browser-login`, `browser-click`, `browser-select`, `browser-expect`, `browser-screenshot`, `browser-snapshot`, `browser-hosts`, `sheet-show`, `cleanup`. `serve` is the private child process `launch` starts. Do not start it yourself.

When the dashboard's screens change, update the feature files with `/maintain-verification-skill`.
