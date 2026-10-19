#!/usr/bin/env python3
"""Drive a local GetRichBot household dashboard with fake sheet rows.

This never starts python -m getrichbot.bot and never accepts a public URL.
The sheet is an in-memory stand-in, the same shape the dashboard tests use.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse


REPO = Path(__file__).resolve().parents[4]
SCRIPT = Path(__file__).resolve()
EVIDENCE = REPO / ".cursor" / "skills" / "verify-getrichbot" / "artifacts"
STATE = Path(os.environ.get("GETRICHBOT_VERIFY_STATE", "/tmp/getrichbot-verify"))
INSTANCE = STATE / "instance.json"
DEFAULT_PORT = 41731
VERIFY_TOKEN = "verify-local-dashboard-token"
TODAY = date(2026, 9, 26)
VIEWERS = {
    "alex": {"id": 111, "first_name": "Alex", "label": "Alex"},
    "sam": {"id": 222, "first_name": "Sam", "label": "Sam"},
}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "telegram.org"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the local household dashboard.")
    sub = parser.add_subparsers(dest="command", required=True)

    launch = sub.add_parser("launch")
    launch.add_argument("--port", type=int, default=_default_port())
    launch.set_defaults(func=cmd_launch)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, required=True)
    serve.set_defaults(func=cmd_serve)

    opened = sub.add_parser("browser-open")
    opened.set_defaults(func=cmd_browser_open)

    click = sub.add_parser("browser-click")
    click.add_argument("--role", required=True)
    click.add_argument("--name", required=True)
    click.add_argument("--wait-text")
    click.set_defaults(func=cmd_browser_click)

    select = sub.add_parser("browser-select")
    select.add_argument("--label", required=True)
    select.add_argument("--value", required=True)
    select.add_argument("--wait-text")
    select.set_defaults(func=cmd_browser_select)

    login = sub.add_parser("browser-login")
    login.add_argument("--as", dest="person", choices=sorted(VIEWERS), required=True)
    login.add_argument("--wait-text")
    login.set_defaults(func=cmd_browser_login)

    expect = sub.add_parser("browser-expect")
    expect.add_argument("--contains", action="append", default=[])
    expect.add_argument("--absent", action="append", default=[])
    expect.set_defaults(func=cmd_browser_expect)

    shot = sub.add_parser("browser-screenshot")
    shot.add_argument("--path", required=True)
    shot.set_defaults(func=cmd_browser_screenshot)

    snap = sub.add_parser("browser-snapshot")
    snap.add_argument("--aria", action="store_true")
    snap.add_argument("--path", required=True)
    snap.set_defaults(func=cmd_browser_snapshot)

    hosts = sub.add_parser("browser-hosts")
    hosts.add_argument("--path", required=True)
    hosts.set_defaults(func=cmd_browser_hosts)

    sheet = sub.add_parser("sheet-show")
    sheet.add_argument("--scope", choices=("both", "Alex", "Sam"), required=True)
    sheet.add_argument("--path", required=True)
    sheet.set_defaults(func=cmd_sheet_show)

    cleanup = sub.add_parser("cleanup")
    cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    args.func(args)


def cmd_launch(args: argparse.Namespace) -> None:
    _ensure_dotenv_python()
    port = args.port
    if port <= 0 or port > 65535:
        raise SystemExit("Port must be a number from 1 to 65535.")
    existing = _read_instance()
    if existing and _pid_alive(existing.get("pid")) and _cmdline_matches(existing["pid"], "serve"):
        raise SystemExit(
            f"A verification dashboard is already running at {existing['url']}. "
            "Run cleanup before launch."
        )
    if existing:
        _remove_state()
    STATE.mkdir(parents=True, exist_ok=True)
    log_path = STATE / "server.log"
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT), "serve", "--port", str(port)],
        cwd=str(STATE),
        env=_child_env(),
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise SystemExit(f"The verification dashboard stopped before it was ready.\n{detail}")
        if _page_ready(url):
            ready = True
            break
        time.sleep(0.2)
    if not ready:
        _signal_group(proc.pid, signal.SIGKILL)
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise SystemExit(f"The verification dashboard did not answer on {url}.\n{detail}")
    instance = {
        "pid": proc.pid,
        "port": port,
        "url": f"http://127.0.0.1:{port}",
        "host": "127.0.0.1",
        "sheets": "fake",
        "chrome_pid": None,
        "chrome_port": None,
        "chrome_profile": str(STATE / "chrome-profile"),
    }
    _write_instance(instance)
    print(f"ready url={instance['url']} pid={proc.pid} sheets=fake")


def cmd_doctor(_args: argparse.Namespace) -> None:
    instance = _require_instance()
    pid = instance["pid"]
    url = instance["url"]
    problems: list[str] = []
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        problems.append(f"url is {url}, expected http://127.0.0.1:<port>")
    if not _pid_alive(pid) or not _cmdline_matches(pid, "serve"):
        problems.append("server process is not the verification dashboard this run started")
    elif "getrichbot.bot" in _cmdline(pid):
        problems.append("server command is the live bot")
    owner = _listen_owner(instance["port"])
    if owner != pid:
        problems.append(f"port {instance['port']} is owned by pid {owner}, not {pid}")
    if _listen_address(instance["port"]) != "127.0.0.1":
        problems.append("dashboard is not bound to 127.0.0.1")
    try:
        status, body = _http("GET", url + "/")
    except Exception as error:
        status, body = 0, str(error).encode()
        problems.append(f"home page did not answer: {error}")
    if status != 200 or b"Household money" not in body:
        problems.append("home page is not the household dashboard")
    if b"Sample cafe" in body or b"Sample market" in body:
        problems.append("locked home page already contains sheet rows")
    try:
        api_status, api_body = _http("GET", url + "/api/dashboard")
    except Exception as error:
        api_status, api_body = 0, str(error).encode()
        problems.append(f"dashboard API did not answer: {error}")
    if api_status != 401 or b"Sample cafe" in api_body:
        problems.append("dashboard API is readable without a login")
    try:
        config_status, config_body = _http("GET", url + "/api/config")
        config = json.loads(config_body.decode())
    except Exception as error:
        config_status, config = 0, {"error": str(error)}
        problems.append(f"config did not answer: {error}")
    if config_status != 200 or config.get("bot_username"):
        problems.append("config names a Telegram bot, so the login widget would call Telegram")
    outside = _outside_loopback(pid)
    if outside:
        problems.append("server has a network connection outside this computer: " + ", ".join(outside))
    if instance.get("sheets") != "fake" or instance.get("host") != "127.0.0.1":
        problems.append("instance record is not a local fake-sheet dashboard")
    if problems:
        print("not ok")
        for problem in problems:
            print(problem)
        raise SystemExit(1)
    print("ok")
    print(f"url={url}")
    print(f"pid={pid}")
    print(f"port={instance['port']}")
    print("bind=127.0.0.1")
    print("sheets=fake")
    print("page=Household money")
    print("dashboard_without_login=401")
    print("bot_username=")
    print("server_network=loopback-only")
    print("not_the_live_bot=true")


def cmd_serve(args: argparse.Namespace) -> None:
    _block_dotenv()
    for key in list(os.environ):
        upper = key.upper()
        if upper == "PORT" or upper.startswith(
            ("TELEGRAM", "GOOGLE", "OPENAI", "GMAIL", "EMAIL", "EXA", "RAILWAY", "DASHBOARD", "CATEGORIES")
        ):
            os.environ.pop(key, None)
    sys.path.insert(0, str(REPO))
    os.chdir(STATE)
    from getrichbot.categories import DEFAULT_CATEGORY_CONFIG, configure_category_config
    from getrichbot.dashboard_http import DashboardApp, DashboardContext, DashboardHTTPServer

    configure_category_config({**DEFAULT_CATEGORY_CONFIG, "source": "verification-public-example"})
    sheets = FakeSheets()
    context = DashboardContext(
        bot_token=VERIFY_TOKEN,
        allowed_user_ids={111, 222},
        label_for_user=_label_for_user,
        raw_sheet="Raw Expenses",
        card_usage_sheet="Card Usage",
        payment_methods_sheet="Payment Methods",
        card_limits_sheet="Card Limits",
        sheets=sheets,
        today=lambda: TODAY,
    )
    if ":" in context.bot_token or context.bot_token != VERIFY_TOKEN:
        raise SystemExit("Refusing to start with a token that is not the local verification token.")
    app = DashboardApp(context)
    DashboardHTTPServer.allow_reuse_address = True
    try:
        server = DashboardHTTPServer("127.0.0.1", args.port, app)
    except OSError as error:
        raise SystemExit(f"Could not listen on 127.0.0.1:{args.port}: {error}") from error
    print(f"verification dashboard ready url=http://127.0.0.1:{args.port} sheets=fake", flush=True)
    server.serve_forever()


def cmd_browser_open(_args: argparse.Namespace) -> None:
    instance = _require_instance()
    if instance.get("chrome_pid") and _pid_alive(instance["chrome_pid"]):
        print(f"browser already open url={instance['url']}")
        return
    profile = Path(instance["chrome_profile"])
    if profile.exists():
        shutil.rmtree(profile)
    profile.mkdir(parents=True)
    log_path = STATE / "chrome.log"
    proc = subprocess.Popen(
        [
            _chrome_bin(),
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--window-size=1280,900",
            "--remote-debugging-port=0",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=log_path.open("w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    instance["chrome_pid"] = proc.pid
    _write_instance(instance)
    port = _wait_devtools_port(profile, proc)
    instance["chrome_port"] = port
    _write_instance(instance)
    with _cdp(instance) as cdp:
        cdp.call("Page.enable")
        cdp.call("Page.navigate", {"url": instance["url"] + "/"})
        text = _wait_for_text(cdp, "Household money", timeout=20)
    if "Household money" not in text:
        raise SystemExit("The browser opened a page that is not the household dashboard.\n" + text[:500])
    print(f"browser open url={instance['url']}")


def cmd_browser_click(args: argparse.Namespace) -> None:
    instance = _require_instance()
    with _cdp(instance) as cdp:
        result = _click(cdp, args.role, args.name)
        if args.wait_text:
            _wait_for_text(cdp, args.wait_text)
    print(f"clicked role={args.role} name={result}")


def cmd_browser_select(args: argparse.Namespace) -> None:
    instance = _require_instance()
    with _cdp(instance) as cdp:
        _select(cdp, args.label, args.value)
        if args.wait_text:
            _wait_for_text(cdp, args.wait_text)
    print(f"selected label={args.label} value={args.value}")


def cmd_browser_login(args: argparse.Namespace) -> None:
    instance = _require_instance()
    person = VIEWERS[args.person]
    payload = _sign_widget(person["id"], person["first_name"])
    expression = "window.onTelegramAuth(%s); true" % json.dumps(payload)
    with _cdp(instance) as cdp:
        _evaluate(cdp, expression)
        if args.wait_text:
            _wait_for_text(cdp, args.wait_text)
    print(f"login as={args.person} label={person['label']}")


def cmd_browser_expect(args: argparse.Namespace) -> None:
    instance = _require_instance()
    with _cdp(instance) as cdp:
        text = _body_text(cdp)
    missing = [item for item in args.contains if item not in text]
    present = [item for item in args.absent if item in text]
    if missing or present:
        print("not ok")
        for item in missing:
            print(f"missing: {item}")
        for item in present:
            print(f"unexpected: {item}")
        print(text[:1500])
        raise SystemExit(1)
    print("ok")


def cmd_browser_screenshot(args: argparse.Namespace) -> None:
    instance = _require_instance()
    path = _evidence_path(args.path)
    with _cdp(instance) as cdp:
        cdp.call("Page.enable")
        result = cdp.call("Page.captureScreenshot", {"format": "png"})
    path.write_bytes(base64.b64decode(result["data"]))
    print(f"screenshot {path}")


def cmd_browser_snapshot(args: argparse.Namespace) -> None:
    if not args.aria:
        raise SystemExit("browser snapshot requires --aria")
    instance = _require_instance()
    path = _evidence_path(args.path)
    with _cdp(instance) as cdp:
        cdp.call("Accessibility.enable")
        tree = cdp.call("Accessibility.getFullAXTree")
    path.write_text(_aria_text(tree), encoding="utf-8")
    print(f"snapshot {path}")


def cmd_browser_hosts(args: argparse.Namespace) -> None:
    instance = _require_instance()
    path = _evidence_path(args.path)
    with _cdp(instance) as cdp:
        hosts = _evaluate(
            cdp,
            """
            [...new Set(performance.getEntriesByType('resource').map((entry) => {
              try { return new URL(entry.name).host; } catch (error) { return entry.name; }
            }))].sort()
            """,
        )
    hosts = hosts or []
    path.write_text("\n".join(hosts) + ("\n" if hosts else ""), encoding="utf-8")
    bad = [host for host in hosts if _host_is_forbidden(host)]
    if bad:
        print("not ok")
        print("forbidden hosts: " + ", ".join(bad))
        raise SystemExit(1)
    print("ok")
    for host in hosts:
        print(host)


def cmd_sheet_show(args: argparse.Namespace) -> None:
    instance = _require_instance()
    path = _evidence_path(args.path)
    expression = """
        (async () => {
          const response = await fetch(%s, {credentials: 'same-origin'});
          const body = await response.text();
          return {status: response.status, body};
        })()
    """ % json.dumps(f"/api/dashboard?scope={args.scope}&cards=mine")
    with _cdp(instance) as cdp:
        result = _evaluate(cdp, expression)
    if not isinstance(result, dict) or result.get("status") != 200:
        raise SystemExit(f"Sheet read failed: {result}")
    body = result["body"]
    payload = json.loads(body)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"sheet scope={args.scope} path={path}")
    print(f"viewer={payload.get('viewer_label')} rows={len(payload.get('recent', []))}")


def cmd_cleanup(_args: argparse.Namespace) -> None:
    instance = _read_instance()
    if not instance:
        print("nothing to stop")
        return
    chrome_pid = instance.get("chrome_pid")
    profile = instance.get("chrome_profile", "")
    if chrome_pid and _pid_alive(chrome_pid):
        if profile and profile in _cmdline(chrome_pid):
            _signal_group(chrome_pid, signal.SIGTERM)
            _wait_dead(chrome_pid)
            if _pid_alive(chrome_pid):
                _signal_group(chrome_pid, signal.SIGKILL)
        else:
            raise SystemExit(f"Refusing to stop pid {chrome_pid}; it is not the verification browser.")
    server_pid = instance.get("pid")
    if server_pid and _pid_alive(server_pid):
        if _cmdline_matches(server_pid, "serve"):
            _signal_group(server_pid, signal.SIGTERM)
            _wait_dead(server_pid)
            if _pid_alive(server_pid):
                _signal_group(server_pid, signal.SIGKILL)
        else:
            raise SystemExit(f"Refusing to stop pid {server_pid}; it is not the verification dashboard.")
    _remove_state()
    print("stopped")
    print(f"evidence={EVIDENCE}")


class FakeSheets:
    """In-memory stand-in. Reads and row edits stay in this process."""

    def __init__(self) -> None:
        from getrichbot.cards import parse_payment_config
        from getrichbot.models import ExpenseRecord

        self.records = [
            ExpenseRecord(
                row_number=2,
                entry_id="aa1001",
                timestamp="12:00:00",
                expense_date="2026-09-20",
                month="2026-09",
                logged_by="Alex",
                raw_input="sample cafe",
                amount=Decimal("18.50"),
                category="Food",
                description="Sample cafe",
                input_type="text",
                status="Confirmed",
                transaction_type="Expense",
                payment_method="Sample Visa",
                payment_owner="Alex",
            ),
            ExpenseRecord(
                row_number=3,
                entry_id="bb2002",
                timestamp="09:15:00",
                expense_date="2026-09-18",
                month="2026-09",
                logged_by="Sam",
                raw_input="sample market",
                amount=Decimal("12.40"),
                category="Groceries",
                description="Sample market",
                input_type="text",
                status="Confirmed",
                transaction_type="Expense",
                payment_method="Sample Mastercard",
                payment_owner="Sam",
            ),
        ]
        self.usage: list = []
        self.summary_rows: list = []
        self.keywords: list = []
        self.config = parse_payment_config(
            [
                ["Payment Method", "Owner", "Type", "Cycle Type", "Cycle Start Day", "Active"],
                ["Sample Visa", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
                ["Sample Cashback", "Alex", "Credit Card", "Calendar", "1", "TRUE"],
                ["Sample Mastercard", "Sam", "Credit Card", "Calendar", "1", "TRUE"],
            ],
            [
                ["Payment Method", "Owner", "Category", "Payment Channel", "Limit Amount", "Active"],
                ["Sample Visa", "Alex", "All", "All", "100", "TRUE"],
                ["Sample Mastercard", "Sam", "All", "All", "200", "TRUE"],
            ],
        )

    def get_expense_records(self, sheet_name, timeout_seconds=None):
        return list(self.records)

    def get_card_usage_records(self, sheet_name):
        return list(self.usage)

    def get_payment_config(self, payment_methods_sheet, card_limits_sheet):
        return self.config

    def update_expense_record(self, sheet_name, row_number, **kwargs):
        updated = []
        for record in self.records:
            if record.row_number == row_number:
                changes = {
                    key: value
                    for key, value in kwargs.items()
                    if value is not None and key in record.__dataclass_fields__
                }
                record = replace(record, **changes)
            updated.append(record)
        self.records = updated

    def update_card_usage_record(self, sheet_name, row_number, **kwargs):
        return None

    def update_monthly_summary(self, sheet_name, rows, timeout_seconds=None):
        self.summary_rows = rows

    def upsert_category_keyword(self, sheet_name, keyword, category):
        self.keywords.append((sheet_name, keyword, category))

    def delete_entry_by_id(self, sheet_name, entry_id, logged_by=None):
        if sheet_name == "Card Usage":
            before = len(self.usage)
            self.usage = [row for row in self.usage if row.entry_id.lower() != entry_id.lower()]
            return len(self.usage) != before
        before = len(self.records)
        self.records = [row for row in self.records if row.entry_id.lower() != entry_id.lower()]
        return len(self.records) != before


def _label_for_user(user_id: int) -> str | None:
    for person in VIEWERS.values():
        if person["id"] == user_id:
            return person["label"]
    return None


def _sign_widget(user_id: int, first_name: str) -> dict:
    auth_date = int(time.time())
    fields = {"auth_date": str(auth_date), "first_name": first_name, "id": str(user_id)}
    data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hashlib.sha256(VERIFY_TOKEN.encode()).digest()
    digest = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    return {"auth_date": auth_date, "first_name": first_name, "id": user_id, "hash": digest}


def _default_port() -> int:
    raw = os.environ.get("GETRICHBOT_VERIFY_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        return int(raw)
    except ValueError as error:
        raise SystemExit("GETRICHBOT_VERIFY_PORT must be a number.") from error


def _ensure_dotenv_python() -> None:
    try:
        import dotenv  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    venv_python = REPO / ".venv" / "bin" / "python"
    if venv_python.exists() and _python_has_dotenv(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])
    subprocess.check_call([sys.executable, "-m", "venv", str(REPO / ".venv")])
    subprocess.check_call(
        [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "python-dotenv==1.0.1"]
    )
    os.execv(str(venv_python), [str(venv_python), *sys.argv])


def _python_has_dotenv(python: Path) -> bool:
    result = subprocess.run([str(python), "-c", "import dotenv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def _block_dotenv() -> None:
    import dotenv

    dotenv.load_dotenv = lambda *args, **kwargs: False


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if upper == "PORT" or upper.startswith(
            ("TELEGRAM", "GOOGLE", "OPENAI", "GMAIL", "EMAIL", "EXA", "RAILWAY", "DASHBOARD", "CATEGORIES")
        ):
            env.pop(key, None)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _page_ready(url: str) -> bool:
    try:
        status, body = _http("GET", url)
    except Exception:
        return False
    return status == 200 and b"Household money" in body


def _http(method: str, url: str, body: bytes | None = None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _read_instance() -> dict | None:
    if not INSTANCE.exists():
        return None
    return json.loads(INSTANCE.read_text(encoding="utf-8"))


def _require_instance() -> dict:
    instance = _read_instance()
    if not instance:
        raise SystemExit("No verification dashboard is running. Run launch first.")
    parsed = urlparse(instance["url"])
    if parsed.hostname != "127.0.0.1":
        raise SystemExit(f"Refusing to drive {instance['url']}.")
    return instance


def _write_instance(instance: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    temporary = INSTANCE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(instance, indent=2) + "\n", encoding="utf-8")
    temporary.replace(INSTANCE)


def _remove_state() -> None:
    if STATE.exists():
        shutil.rmtree(STATE)


def _pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")


def _cmdline_matches(pid: int, word: str) -> bool:
    command = _cmdline(pid)
    return str(SCRIPT) in command and word in command.split()


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            return


def _wait_dead(pid: int, timeout: float = 3) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.1)


def _listen_owner(port: int) -> int | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
    return None


def _listen_address(port: int) -> str | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines()[1:]:
        if f":{port}" in line and "127.0.0.1" in line:
            return "127.0.0.1"
        if f":{port}" in line and "*" in line:
            return "*"
    return None


def _outside_loopback(pid: int) -> list[str]:
    result = subprocess.run(["lsof", "-nP", "-a", "-p", str(pid), "-i"], check=False, capture_output=True, text=True)
    outside: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        name = line.split()[-1] if line.split() else ""
        if "127.0.0.1" in name or "[::1]" in name or name.endswith("(LISTEN)"):
            continue
        outside.append(name)
    return outside


def _chrome_bin() -> str:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("Chrome is not installed, so the dashboard page cannot be driven.")


def _wait_devtools_port(profile: Path, proc: subprocess.Popen) -> int:
    marker = profile / "DevToolsActivePort"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = (STATE / "chrome.log").read_text(encoding="utf-8", errors="replace")[-2000:]
            raise SystemExit(f"Chrome stopped before its debug port opened.\n{detail}")
        if marker.exists():
            port_text = marker.read_text(encoding="utf-8").splitlines()[0].strip()
            if port_text.isdigit():
                return int(port_text)
        time.sleep(0.1)
    raise SystemExit("Chrome did not open a debug port.")


class _Cdp:
    def __init__(self, debug_port: int):
        self.debug_port = debug_port
        self.socket: socket.socket | None = None
        self.next_id = 0

    def __enter__(self) -> _Cdp:
        targets = json.loads(_http("GET", f"http://127.0.0.1:{self.debug_port}/json/list")[1].decode())
        page = next((item for item in targets if item.get("type") == "page"), None)
        if page is None:
            raise SystemExit("Chrome has no page to drive.")
        websocket = urlparse(page["webSocketDebuggerUrl"])
        sock = socket.create_connection((websocket.hostname, websocket.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {websocket.path} HTTP/1.1\r\n"
            f"Host: {websocket.hostname}:{websocket.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise SystemExit("Chrome debug connection closed.")
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise SystemExit("Chrome did not accept the debug connection.")
        self.socket = sock
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def call(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        message_id = self.next_id
        self._send({"id": message_id, "method": method, "params": params or {}})
        deadline = time.time() + 20
        while time.time() < deadline:
            message = self._recv()
            if message is None:
                continue
            if message.get("id") == message_id:
                if "error" in message:
                    raise SystemExit(f"{method} failed: {message['error']}")
                return message.get("result") or {}
        raise SystemExit(f"{method} timed out.")

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        header.extend(masked)
        assert self.socket is not None
        self.socket.sendall(header)

    def _recv(self) -> dict | None:
        assert self.socket is not None
        self.socket.settimeout(20)
        first = _recv_exact(self.socket, 2)
        opcode = first[0] & 0x0F
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", _recv_exact(self.socket, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _recv_exact(self.socket, 8))[0]
        if first[1] & 0x80:
            mask = _recv_exact(self.socket, 4)
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(_recv_exact(self.socket, length)))
        else:
            payload = _recv_exact(self.socket, length) if length else b""
        if opcode == 0x8:
            raise SystemExit("Chrome closed the debug connection.")
        if opcode == 0x9:
            self._send_pong(payload)
            return None
        if opcode == 0xA:
            return None
        if opcode != 0x1:
            return None
        return json.loads(payload.decode())

    def _send_pong(self, payload: bytes) -> None:
        assert self.socket is not None
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytearray([0x8A, 0x80 | len(payload)])
        header.extend(mask)
        header.extend(masked)
        self.socket.sendall(header)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SystemExit("Chrome debug connection closed.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _cdp(instance: dict) -> _Cdp:
    port = instance.get("chrome_port")
    if not port or not instance.get("chrome_pid") or not _pid_alive(instance["chrome_pid"]):
        raise SystemExit("The verification browser is not open. Run browser-open first.")
    return _Cdp(port)


def _evaluate(cdp: _Cdp, expression: str):
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "awaitPromise": True, "returnByValue": True},
    )
    details = result.get("exceptionDetails")
    if details:
        text = details.get("text") or details.get("exception", {}).get("description") or "page script failed"
        raise SystemExit(text)
    remote = result.get("result") or {}
    if remote.get("subtype") == "error":
        raise SystemExit(remote.get("description") or "page script failed")
    return remote.get("value")


def _body_text(cdp: _Cdp) -> str:
    return _evaluate(cdp, "document.body ? document.body.innerText : ''") or ""


def _wait_for_text(cdp: _Cdp, text: str, timeout: float = 8) -> str:
    deadline = time.time() + timeout
    latest = ""
    while time.time() < deadline:
        latest = _body_text(cdp)
        if text in latest:
            return latest
        time.sleep(0.15)
    raise SystemExit(f"Timed out waiting for {text!r}.\n{latest[:1500]}")


def _click(cdp: _Cdp, role: str, name: str) -> str:
    expression = """
        (() => {
          const role = %s;
          const name = %s;
          const accessibleName = (el) => (el.getAttribute('aria-label') || el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
          const implicitRole = (el) => {
            const explicit = el.getAttribute('role');
            if (explicit) return explicit;
            const tag = el.tagName.toLowerCase();
            if (tag === 'button') return 'button';
            if (tag === 'a') return 'link';
            if (tag === 'select') return 'combobox';
            if (tag === 'textarea') return 'textbox';
            if (tag === 'input') return (el.getAttribute('type') || 'text').toLowerCase() === 'checkbox' ? 'checkbox' : 'textbox';
            if (/^h[1-6]$/.test(tag)) return 'heading';
            return '';
          };
          const matches = [...document.querySelectorAll('button, a, input, select, textarea, [role]')].filter((el) => implicitRole(el) === role && accessibleName(el) === name);
          if (matches.length !== 1) {
            return {ok: false, count: matches.length, choices: matches.map(accessibleName)};
          }
          matches[0].click();
          return {ok: true, name: accessibleName(matches[0])};
        })()
    """ % (json.dumps(role), json.dumps(name))
    result = _evaluate(cdp, expression)
    if not result or not result.get("ok"):
        raise SystemExit(f"Could not click {role} {name!r}: {result}")
    return result["name"]


def _select(cdp: _Cdp, label: str, value: str) -> None:
    expression = """
        (() => {
          const label = %s;
          const value = %s;
          const select = [...document.querySelectorAll('select')].find((el) => (el.getAttribute('aria-label') || '') === label);
          if (!select) return {ok: false, reason: 'missing'};
          const option = [...select.options].find((item) => item.value === value || item.textContent.trim() === value);
          if (!option) return {ok: false, reason: 'value', choices: [...select.options].map((item) => item.value)};
          select.value = option.value;
          select.dispatchEvent(new Event('change', {bubbles: true}));
          return {ok: true};
        })()
    """ % (json.dumps(label), json.dumps(value))
    result = _evaluate(cdp, expression)
    if not result or not result.get("ok"):
        raise SystemExit(f"Could not choose {value!r} in {label!r}: {result}")


def _aria_text(tree: dict) -> str:
    nodes = {node["nodeId"]: node for node in tree.get("nodes", [])}
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for node in tree.get("nodes", []):
        node_id = node["nodeId"]
        parent = node.get("parentId")
        if parent in nodes:
            children.setdefault(parent, []).append(node_id)
        else:
            roots.append(node_id)

    def role_name(node: dict) -> tuple[str, str]:
        role = ((node.get("role") or {}).get("value")) or ""
        name = ((node.get("name") or {}).get("value")) or ""
        return role, name.replace("\n", " ").strip()

    lines: list[str] = []

    def walk(node_id: str, depth: int) -> None:
        node = nodes[node_id]
        role, name = role_name(node)
        if role not in {"generic", "none", "InlineTextBox", "StaticText", "ignored"} or name:
            if role or name:
                lines.append(f"{'  ' * depth}{role} {name}".rstrip())
        for child_id in children.get(node_id, []):
            walk(child_id, depth + 1)

    for root in roots:
        walk(root, 0)
    return "\n".join(lines) + "\n"


def _evidence_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO / candidate
    candidate = candidate.resolve()
    root = EVIDENCE.resolve()
    if candidate != root and root not in candidate.parents:
        raise SystemExit(f"Evidence must stay under {root}.")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def _host_is_forbidden(host: str) -> bool:
    lowered = host.lower()
    if lowered.startswith("[") and "]" in lowered:
        name = lowered[1:lowered.index("]")]
    else:
        name = lowered.split(":")[0]
    if name in ALLOWED_HOSTS or name.endswith(".telegram.org"):
        return False
    return True


if __name__ == "__main__":
    main()
