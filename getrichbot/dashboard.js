const ERROR_TEXT = {
  invalid: "Telegram could not confirm who you are. Nothing was opened.",
  expired: "That Telegram confirmation is too old. Try again.",
  not_allowed: "This Telegram account is not allowed to open the dashboard.",
  locked: "This page is locked.",
  sheet: "I could not read the Google Sheet just now. Nothing was changed. Try again in a moment.",
  scope: "Choose your own spending, or both."
};

const state = {
  view: "recent",
  scope: "mine",
  payload: null,
  message: "",
  loggedOut: false,
  botUsername: ""
};

const app = document.querySelector("#app");

window.onTelegramAuth = function (user) {
  loginWithWidget(user);
};

window.addEventListener("load", boot);

function telegramInitData() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  return telegram && telegram.initData ? telegram.initData : "";
}

async function boot() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  if (telegram && telegram.ready) {
    telegram.ready();
    if (telegram.expand) telegram.expand();
  }
  try {
    const config = await fetch("/api/config", { credentials: "same-origin" }).then((response) => response.json());
    state.botUsername = config.bot_username || "";
  } catch (error) {
    state.botUsername = "";
  }
  if (!state.loggedOut && telegramInitData()) {
    const result = await postJson("/api/session/webapp", { init_data: telegramInitData() });
    if (result.ok) {
      await loadDashboard();
      return;
    }
    state.message = ERROR_TEXT[result.error] || ERROR_TEXT.locked;
    renderLogin();
    return;
  }
  const session = await fetch("/api/session", { credentials: "same-origin" });
  if (session.ok) {
    await loadDashboard();
    return;
  }
  renderLogin();
}

async function loadDashboard() {
  state.message = "";
  renderLoading();
  const response = await fetch("/api/dashboard?scope=" + encodeURIComponent(state.scope), {
    credentials: "same-origin",
    headers: telegramInitData() && !state.loggedOut ? { "X-Telegram-Init-Data": telegramInitData() } : {}
  });
  const body = await response.json().catch(() => ({ ok: false, error: "sheet" }));
  if (!response.ok) {
    state.payload = null;
    state.message = ERROR_TEXT[body.error] || ERROR_TEXT.sheet;
    if (response.status === 401) renderLogin();
    else renderMessage(state.message);
    return;
  }
  state.payload = body;
  render();
}

async function loginWithWidget(user) {
  const result = await postJson("/api/session/widget", user);
  if (!result.ok) {
    state.message = ERROR_TEXT[result.error] || ERROR_TEXT.locked;
    renderLogin();
    return;
  }
  state.loggedOut = false;
  state.scope = "mine";
  state.view = "recent";
  await loadDashboard();
}

async function logout() {
  await fetch("/api/session/logout", { method: "POST", credentials: "same-origin" });
  state.loggedOut = true;
  state.payload = null;
  state.view = "recent";
  state.message = "Logged out. This browser has forgotten you.";
  renderLogin();
}

async function postJson(path, payload) {
  try {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const body = await response.json();
    return { ok: response.ok && body.ok, error: body.error || "locked", label: body.label || "" };
  } catch (error) {
    return { ok: false, error: "sheet" };
  }
}

function renderLoading() {
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark(), el("h1", "", "Household money"), el("p", "lede", "Reading the sheet..."));
  screen.append(card);
  app.append(screen);
}

function renderMessage(text) {
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark(), el("h1", "", "Household money"), el("p", "notice", text));
  const retry = el("button", "primary", "Try again");
  retry.type = "button";
  retry.addEventListener("click", () => loadDashboard());
  card.append(retry);
  screen.append(card);
  app.append(screen);
}

function renderLogin() {
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark());
  card.append(el("h1", "", "Household money"));
  card.append(el("p", "lede", "A private page for the two people who already use the Telegram bot."));
  const notice = el("div", "notice");
  notice.textContent = state.message || "This page is locked until Telegram confirms who you are.";
  card.append(notice);
  if (telegramInitData()) {
    const cont = el("button", "primary", "Continue in Telegram");
    cont.type = "button";
    cont.addEventListener("click", async () => {
      state.loggedOut = false;
      const result = await postJson("/api/session/webapp", { init_data: telegramInitData() });
      if (!result.ok) {
        state.message = ERROR_TEXT[result.error] || ERROR_TEXT.locked;
        renderLogin();
        return;
      }
      await loadDashboard();
    });
    card.append(cont);
  }
  const slot = el("div", "widget-slot");
  slot.id = "telegram-login";
  card.append(slot);
  if (!state.botUsername) {
    card.append(el("p", "sub", "Log in with Telegram will show here after the bot has connected. On a phone, use the Dashboard button in the chat."));
  }
  card.append(el("p", "fine", "On a computer, use Log in with Telegram. After it works, this browser remembers you for 30 days. Log out forgets it immediately. On a shared computer, log out when you are done."));
  screen.append(card);
  app.append(screen);
  mountWidget();
}

function mountWidget() {
  if (!state.botUsername) return;
  const slot = document.querySelector("#telegram-login");
  if (!slot) return;
  const script = document.createElement("script");
  script.async = true;
  script.src = "https://telegram.org/js/telegram-widget.js?22";
  script.setAttribute("data-telegram-login", state.botUsername);
  script.setAttribute("data-size", "large");
  script.setAttribute("data-radius", "12");
  script.setAttribute("data-onauth", "onTelegramAuth(user)");
  slot.append(script);
}

function render() {
  app.replaceChildren();
  const shell = el("div");
  shell.append(renderTop());
  const main = el("main");
  const wrap = el("div", "wrap");
  if (state.view === "recent") wrap.append(renderRecent());
  else if (state.view === "cards") wrap.append(renderCards());
  else if (state.view === "month") wrap.append(renderMonth());
  else if (state.view === "raw") wrap.append(renderRaw());
  else wrap.append(renderLater());
  main.append(wrap);
  shell.append(main);
  app.append(shell);
}

function renderTop() {
  const header = el("header", "top");
  const wrap = el("div", "wrap");
  const row = el("div", "top-row");
  const brand = el("div");
  brand.append(mark());
  brand.append(el("div", "who", "Signed in as " + state.payload.viewer_label));
  const out = el("button", "signout", "Log out");
  out.type = "button";
  out.addEventListener("click", logout);
  row.append(brand, out);
  const nav = el("nav", "nav-row");
  nav.setAttribute("aria-label", "Dashboard sections");
  [
    ["recent", "Recent"],
    ["cards", "Cards"],
    ["month", "Month"],
    ["raw", "Raw entries"],
    ["later", "Agent eval"]
  ].forEach(([id, label]) => {
    const button = el("button", "nav-btn" + (id === "later" ? " later" : ""), label);
    button.type = "button";
    if (state.view === id) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => {
      state.view = id;
      render();
    });
    nav.append(button);
  });
  wrap.append(row, nav);
  header.append(wrap);
  return header;
}

function renderRecent() {
  const section = el("section");
  const head = pageHead(
    "Recent transactions",
    "Switch between your own rows and both of you. This follows who logged the row, not whose card paid."
  );
  const filters = el("div", "filters");
  [
    ["mine", state.payload.viewer_label + " only"],
    ["both", "Both"]
  ].forEach(([id, label]) => {
    const chip = el("button", "chip", label);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(state.scope === id));
    chip.addEventListener("click", () => {
      if (state.scope === id) return;
      state.scope = id;
      loadDashboard();
    });
    filters.append(chip);
  });
  head.append(filters);
  section.append(head);
  section.append(el("p", "sub", "Fixing a category or a card from this page comes later. The button below does not change the sheet."));
  if (state.payload.recent_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.recent_limit + " confirmed rows."));
  }
  if (!state.payload.recent.length) section.append(el("p", "empty", "No confirmed rows for this view."));
  state.payload.recent.forEach((row) => section.append(renderTransaction(row)));
  return section;
}

function renderTransaction(row) {
  const article = el("article", "tx");
  const top = el("div", "tx-top");
  top.append(el("div", "merchant", row.description || "No description"));
  top.append(el("div", "amount", money(row.amount)));
  const meta = el("div", "tx-meta");
  meta.append(el("span", "tx-date", prettyDate(row.date)));
  meta.append(el("span", "tag", row.kind === "card_only" ? "Card only · not an expense" : (row.category || row.kind)));
  meta.append(el("span", "tx-logged", "Logged by " + row.logged_by));
  if (row.kind === "income") meta.append(el("span", "tx-note", "No card · income is household"));
  else meta.append(el("span", "tx-note", cardLine(row)));
  article.append(top, meta);
  const actions = el("div", "tx-actions");
  const fix = el("button", "text-btn", "Fix tagging");
  fix.type = "button";
  fix.disabled = true;
  fix.title = "Fixing tags comes later. This does not change anything.";
  actions.append(el("span", "later-note", "Comes later"), fix);
  article.append(actions);
  return article;
}

function renderCards() {
  const section = el("section");
  const cards = state.payload.cards;
  section.append(pageHead(
    "Card summary",
    "Credit cards for " + cards.owner + " only, the same idea as the card summary in Telegram. Colours match the bot."
  ));
  section.append(legend());
  if (cards.error) section.append(el("p", "banner", cards.error));
  section.append(el("p", "group-label", "Capped"));
  if (!cards.capped.length) section.append(el("p", "sub", "No capped cards."));
  else section.append(cardGrid(cards.capped));
  section.append(el("p", "group-label", "Uncapped"));
  if (!cards.uncapped.length) section.append(el("p", "sub", "No uncapped cards. A card with no limit still shows here when one exists."));
  else section.append(cardGrid(cards.uncapped));
  return section;
}

function cardGrid(cards) {
  const grid = el("div", "card-grid");
  cards.forEach((card) => grid.append(renderCard(card)));
  return grid;
}

function renderCard(card) {
  const article = el("article", "money-card");
  const header = el("header");
  header.append(el("h3", "", card.name));
  if (!card.limits.length) header.append(el("strong", "", "No cap"));
  article.append(header);
  article.append(el("div", "sub", prettyDate(card.period_start) + " to " + prettyDate(card.period_end)));
  if (!card.limits.length) {
    const bar = el("div", "bar");
    const fill = el("span", "fill-neutral");
    fill.style.width = "100%";
    bar.append(fill);
    article.append(bar);
    article.append(el("div", "", money(card.spent) + " this period"));
    article.append(el("div", "sub", "No limit set · still shown"));
    return article;
  }
  card.limits.forEach((limit) => {
    const block = el("div", "limit-block");
    const title = el("div", "tx-top");
    title.append(el("span", "", limit.label));
    title.append(el("strong", "", limit.percent_label + "%"));
    const bar = el("div", "bar");
    const fill = el("span", "fill-" + limit.band);
    const width = Math.min(Number(limit.percent) || 0, 100);
    fill.style.width = width + "%";
    bar.append(fill);
    block.append(title, bar);
    block.append(el("div", "", money(limit.spent) + " of " + money(limit.limit)));
    block.append(el("div", "sub", bandText(limit.band)));
    article.append(block);
  });
  return article;
}

function renderMonth() {
  const section = el("section");
  section.append(pageHead(
    "Monthly summary",
    "Household income, expenses, and what is left. Income is not split between the two of you. Card-only rows are left out."
  ));
  const months = state.payload.months;
  const panel = el("div", "panel scroll");
  panel.append(el("h3", "", "From the raw rows"));
  const table = el("table");
  const head = el("thead");
  const headRow = el("tr");
  headRow.append(el("th", "", ""));
  months.forEach((month) => headRow.append(el("th", "num", month.label)));
  head.append(headRow);
  table.append(head);
  const body = el("tbody");
  monthRows(months).forEach((row) => body.append(row));
  table.append(body);
  panel.append(table);
  section.append(panel);
  section.append(el("p", "sub", "These figures are calculated from Raw Expenses when you open the page. They are not a second copy. Card Usage is not included."));
  return section;
}

function monthRows(months) {
  const rows = [];
  const incomeNames = uniqueNames(months, "income");
  const expenseNames = uniqueNames(months, "expenses");
  incomeNames.forEach((name) => rows.push(amountRow(name, months, "income", "")));
  rows.push(totalRow("Total income", months, "total_income", "total"));
  expenseNames.forEach((name) => rows.push(amountRow(name, months, "expenses", "")));
  rows.push(totalRow("Total expenses", months, "total_expenses", "total"));
  rows.push(totalRow("Net", months, "net", "net"));
  return rows;
}

function amountRow(name, months, key, kind) {
  const row = el("tr", kind);
  row.append(el("td", "", name));
  months.forEach((month) => {
    const found = month[key].find((item) => item.category === name);
    row.append(el("td", "num", found ? money(found.amount) : ""));
  });
  return row;
}

function totalRow(name, months, key, kind) {
  const row = el("tr", kind);
  row.append(el("td", "", name));
  months.forEach((month) => row.append(el("td", "num", money(month[key]))));
  return row;
}

function renderRaw() {
  const section = el("section");
  section.append(pageHead(
    "Raw entries",
    "The same rows as the record book, plus card-only rows that stay out of household spending."
  ));
  if (state.payload.raw_expenses_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.raw_expenses_limit + " raw expense rows."));
  }
  section.append(rawTable(
    "Raw Expenses",
    ["Entry ID", "Date", "Logged by", "Amount", "Category", "Description", "Payment owner", "Payment method", "Channel", "Type", "Status"],
    state.payload.raw_expenses.map((row) => [
      row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.category, row.description,
      row.payment_owner || "—", row.payment_method || "—", row.payment_channel || "—", row.type, row.status
    ])
  ));
  if (state.payload.card_usage_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.card_usage_limit + " card-only rows."));
  }
  section.append(rawTable(
    "Card Usage",
    ["Entry ID", "Date", "Logged by", "Amount", "Payment owner", "Payment method", "Channel", "Description", "Type", "Status"],
    state.payload.card_usage.map((row) => [
      row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.payment_owner || "—",
      row.payment_method || "—", row.payment_channel || "—", row.description, row.type, row.status
    ])
  ));
  section.append(el("p", "sub", "Card Usage counts toward the card, and not toward household expenses or the monthly net figure."));
  return section;
}

function renderLater() {
  const section = el("section");
  const box = el("div", "later-box");
  box.append(el("p", "kicker", "Later · not built"));
  box.append(el("h2", "", "Agent eval"));
  box.append(el("p", "sub", "This is a space for later. It would eventually show whether guesses were right or wrong. There is no score, and there is nothing to turn on."));
  section.append(box);
  return section;
}

function legend() {
  const box = el("div", "legend");
  [
    ["green", "Green", "Under 60%"],
    ["yellow", "Yellow", "60% to 79%"],
    ["orange", "Orange", "80% to 94%"],
    ["red", "Red", "95% or more"]
  ].forEach(([key, name, range]) => {
    const item = el("div");
    const title = el("strong", "");
    title.append(el("i", "swatch band-" + key));
    title.append(document.createTextNode(name));
    item.append(title, el("div", "sub", range));
    box.append(item);
  });
  return box;
}

function pageHead(title, subtitle) {
  const head = el("div", "page-head");
  const text = el("div");
  text.append(el("h2", "", title));
  text.append(el("p", "sub", subtitle));
  head.append(text);
  return head;
}

function mark() {
  const node = el("div", "mark");
  node.append(el("i"));
  node.append(document.createTextNode("Household money"));
  return node;
}

function rawTable(title, headers, records) {
  const panel = el("div", "panel");
  panel.append(el("h3", "", title));
  if (!records.length) {
    panel.append(el("p", "empty", "No rows."));
    return panel;
  }
  const scroller = el("div", "table-scroll");
  const table = el("table");
  const head = el("tr");
  headers.forEach((header, index) => head.append(el("th", index === 3 ? "num" : "", header)));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  records.forEach((record) => {
    const row = el("tr");
    record.forEach((value, index) => row.append(el("td", index === 3 ? "num" : "", value)));
    body.append(row);
  });
  table.append(body);
  scroller.append(table);
  panel.append(scroller);
  return panel;
}

function cardLine(row) {
  if (!row.payment_method) return "No card";
  const owner = row.payment_owner ? " · " + row.payment_owner + "’s" : "";
  return "Card " + row.payment_method + owner;
}

function bandText(band) {
  if (band === "green") return "Green · under 60%";
  if (band === "yellow") return "Yellow · 60% to 79%";
  if (band === "orange") return "Orange · 80% to 94%";
  return "Red · 95% or more";
}

function uniqueNames(months, key) {
  const names = [];
  months.forEach((month) => {
    month[key].forEach((item) => {
      if (!names.includes(item.category)) names.push(item.category);
    });
  });
  return names;
}

function money(text) {
  const raw = String(text);
  const negative = raw.startsWith("-");
  const parts = raw.replace("-", "").split(".");
  const whole = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (parts[1] || "00").slice(0, 2).padEnd(2, "0");
  return (negative ? "-$" : "$") + whole + "." + frac;
}

function prettyDate(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!match) return iso || "";
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return Number(match[3]) + " " + months[Number(match[2]) - 1] + " " + match[1];
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
