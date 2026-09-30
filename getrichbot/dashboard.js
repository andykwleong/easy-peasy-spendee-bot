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
window.addEventListener("resize", () => {
  if (document.querySelector(".nav-row")) syncNavPin();
  else document.body.classList.remove("nav-at-bottom", "nav-under-name");
});

function telegramInitData() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  return telegram && telegram.initData ? telegram.initData : "";
}

function telegramBottomWouldCoverNav() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  if (!telegram || !telegram.initData) return false;
  const insets = [telegram.safeAreaInset, telegram.contentSafeAreaInset];
  for (let index = 0; index < insets.length; index += 1) {
    const inset = insets[index];
    if (inset && Number(inset.bottom) >= 20) return true;
  }
  if (telegram.MainButton && telegram.MainButton.isVisible) return true;
  if (telegram.SecondaryButton && telegram.SecondaryButton.isVisible) return true;
  return false;
}

function syncNavPin() {
  const phone = window.matchMedia("(max-width: 720px)").matches;
  const underName = phone && telegramBottomWouldCoverNav();
  document.body.classList.toggle("nav-at-bottom", phone && !underName);
  document.body.classList.toggle("nav-under-name", underName);
}

function clearNavPin() {
  document.body.classList.remove("nav-at-bottom", "nav-under-name");
}

async function boot() {
  const telegram = window.Telegram && window.Telegram.WebApp;
  if (telegram && telegram.ready) {
    telegram.ready();
    if (telegram.expand) telegram.expand();
    if (telegram.onEvent) telegram.onEvent("viewportChanged", syncNavPin);
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
  clearNavPin();
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark(false), el("h1", "", "Household money"), el("p", "lede", "Reading the sheet..."));
  screen.append(card);
  app.append(screen);
}

function renderMessage(text) {
  clearNavPin();
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark(false), el("h1", "", "Household money"), el("p", "notice", text));
  const retry = el("button", "primary", "Try again");
  retry.type = "button";
  retry.addEventListener("click", () => loadDashboard());
  card.append(retry);
  screen.append(card);
  app.append(screen);
}

function renderLogin() {
  clearNavPin();
  app.replaceChildren();
  const screen = el("div", "login-screen");
  const card = el("section", "login-card");
  card.append(mark(false));
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
    card.append(el("p", "sub", "Log in with Telegram will show here after the bot has connected. You can also open this page from the Dashboard button in the chat."));
  }
  card.append(el("p", "fine", "This browser remembers you for 30 days, and Log out forgets it."));
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
  const shell = el("div", "shell");
  const stack = el("div", "top-stack");
  stack.append(renderTop());
  stack.append(renderNav());
  shell.append(stack);
  const main = el("main");
  const wrap = el("div", "wrap");
  if (state.view === "cards") wrap.append(renderCards());
  else if (state.view === "month") wrap.append(renderMonth());
  else if (state.view === "raw") wrap.append(renderRaw());
  else wrap.append(renderRecent());
  main.append(wrap);
  shell.append(main);
  app.append(shell);
  syncNavPin();
}

function renderTop() {
  const header = el("header", "top");
  const wrap = el("div", "wrap");
  const row = el("div", "top-row");
  const brand = el("div");
  brand.append(mark(true));
  brand.append(el("div", "who", "Signed in as " + state.payload.viewer_label));
  const actions = el("div", "top-actions");
  const refresh = el("button", "signout", "Refresh");
  refresh.type = "button";
  refresh.addEventListener("click", () => loadDashboard());
  const out = el("button", "signout", "Log out");
  out.type = "button";
  out.addEventListener("click", logout);
  actions.append(refresh, out);
  row.append(brand, actions);
  wrap.append(row);
  header.append(wrap);
  return header;
}

function renderNav() {
  const nav = el("nav", "nav-row");
  nav.setAttribute("aria-label", "Dashboard sections");
  [
    ["recent", "Recent"],
    ["cards", "Cards"],
    ["month", "Month"],
    ["raw", "Entries"]
  ].forEach(([id, label]) => {
    const button = el("button", "nav-btn", label);
    button.type = "button";
    if (state.view === id) button.setAttribute("aria-current", "page");
    button.addEventListener("click", () => {
      state.view = id;
      render();
    });
    nav.append(button);
  });
  return nav;
}

function renderRecent() {
  const section = el("section");
  const head = pageHead(
    "Recent",
    "This follows who logged the row, not whose card paid."
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
  section.append(el("p", "sub", "This page cannot edit the sheet, and changes stay in the Telegram chat."));
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
  meta.append(el("span", "", prettyDate(row.date)));
  meta.append(el("span", "tag", row.kind === "card_only" ? "Card only · not an expense" : (row.category || row.kind)));
  meta.append(el("span", "", "Logged by " + row.logged_by));
  if (row.kind === "income") meta.append(el("span", "", "No card · income is household"));
  else meta.append(el("span", "", cardLine(row)));
  article.append(top, meta);
  return article;
}

function renderCards() {
  const section = el("section");
  const cards = state.payload.cards;
  section.append(pageHead("Cards", "Only your cards."));
  if (cards.error) section.append(el("p", "banner", cards.error));
  section.append(el("p", "group-label", "With a limit"));
  if (!cards.capped.length) section.append(el("p", "sub", "No cards with a limit."));
  else section.append(cardGrid(cards.capped));
  section.append(el("p", "group-label", "No limit"));
  if (!cards.uncapped.length) section.append(el("p", "sub", "No cards without a limit."));
  else section.append(cardGrid(cards.uncapped));
  section.append(legend());
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
  if (!card.limits.length) header.append(el("strong", "", money(card.spent)));
  article.append(header);
  article.append(el("div", "sub", prettyDate(card.period_start) + " to " + prettyDate(card.period_end)));
  if (!card.limits.length) {
    article.append(el("p", "no-limit", "No limit"));
    return article;
  }
  card.limits.forEach((limit) => {
    const block = el("div", "limit-block");
    const title = el("div", "tx-top");
    const printed = shownPercent(limit);
    title.append(el("span", "", limit.label));
    title.append(el("strong", "", printed + "%"));
    const bar = el("div", "bar");
    bar.setAttribute("role", "img");
    bar.setAttribute("aria-label", bandText(limit.band) + ", " + printed + " percent");
    const fill = el("span", "fill-" + limit.band);
    fill.setAttribute("aria-hidden", "true");
    const width = Math.min(Number(limit.percent) || 0, 100);
    fill.style.width = width + "%";
    bar.append(fill);
    block.append(title, bar);
    block.append(el("div", "", money(limit.spent) + " of " + money(limit.limit)));
    block.append(el("div", "sub band-label", bandText(limit.band)));
    if (limit.band === "orange" || limit.band === "red") {
      block.append(el("p", "look-note", "This page only looks, and the sheet is still the record."));
    }
    article.append(block);
  });
  return article;
}

function renderMonth() {
  const section = el("section");
  const months = state.payload.months;
  const current = months.find((month) => month.so_far) || months[months.length - 1];
  section.append(renderLeft(current));
  const panel = el("div", "panel scroll");
  panel.append(el("h3", "", "Last month and this month"));
  const table = el("table");
  const caption = el("caption", "sr-only", "Household month. Income is shared. Card-only spend is left out.");
  table.append(caption);
  const head = el("thead");
  const headRow = el("tr");
  headRow.append(el("th", "", "What"));
  months.forEach((month) => headRow.append(el("th", "num", monthHeading(month))));
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

function renderLeft(month) {
  const block = el("div", "left-block");
  block.append(el("h2", "", "Left this month"));
  const figure = el("p", "left-figure" + (isUp(month.net) ? " up" : ""), money(month.net));
  block.append(figure);
  block.append(el("p", "sub", monthHeading(month) + ". Income is shared. Card-only spend is left out."));
  return block;
}

function monthHeading(month) {
  return month.so_far ? month.label + " so far" : month.label;
}

function monthRows(months) {
  const rows = [];
  const incomeNames = uniqueNames(months, "income");
  const expenseNames = uniqueNames(months, "expenses");
  incomeNames.forEach((name) => rows.push(amountRow(name, months, "income", "")));
  rows.push(totalRow("Total income", months, "total_income", "total"));
  expenseNames.forEach((name) => rows.push(amountRow(name, months, "expenses", "")));
  rows.push(totalRow("Total expenses", months, "total_expenses", "total"));
  rows.push(totalRow("Left", months, "net", "net"));
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
  months.forEach((month) => {
    const amount = month[key];
    const up = kind === "net" && isUp(amount);
    row.append(el("td", "num" + (up ? " up" : ""), money(amount)));
  });
  return row;
}

function renderRaw() {
  const section = el("section");
  section.append(pageHead(
    "Entries",
    "The same rows as the sheet. Card-only rows stay out of household spending."
  ));
  if (state.payload.raw_expenses_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.raw_expenses_limit + " raw expense rows."));
  }
  section.append(rawBlock(
    "Raw Expenses",
    ["Entry ID", "Date", "Logged by", "Amount", "Category", "Description", "Payment owner", "Payment method", "Channel", "Type", "Status"],
    state.payload.raw_expenses,
    (row) => [
      row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.category, row.description,
      row.payment_owner || "—", row.payment_method || "—", row.payment_channel || "—", row.type, row.status
    ],
    (row) => row.category || "—",
    (row) => [
      ["Entry id", row.id],
      ["Card", cardField(row)],
      ["Channel", row.payment_channel || "—"],
      ["Payment owner", row.payment_owner || "—"],
      ["Type", row.type || "—"],
      ["Status", row.status || "—"],
      ["Time", row.time || "—"]
    ]
  ));
  if (state.payload.card_usage_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.card_usage_limit + " card-only rows."));
  }
  section.append(rawBlock(
    "Card Usage",
    ["Entry ID", "Date", "Logged by", "Amount", "Payment owner", "Payment method", "Channel", "Description", "Type", "Status"],
    state.payload.card_usage,
    (row) => [
      row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.payment_owner || "—",
      row.payment_method || "—", row.payment_channel || "—", row.description, row.type, row.status
    ],
    () => "Card only · not an expense",
    (row) => [
      ["Entry id", row.id],
      ["Card", cardField(row)],
      ["Channel", row.payment_channel || "—"],
      ["Payment owner", row.payment_owner || "—"],
      ["Type", row.type || "—"],
      ["Status", row.status || "—"],
      ["Time", row.time || "—"]
    ]
  ));
  section.append(el("p", "sub", "Card Usage counts toward the card, and not toward household expenses or what is left this month."));
  return section;
}

function rawBlock(title, headers, rows, toCells, category, fields) {
  const block = el("div", "raw-block");
  block.append(rawTable(title, headers, rows.map(toCells)));
  block.append(rawCards(title, rows, category, fields));
  return block;
}

function rawCards(title, rows, category, fields) {
  const list = el("div", "raw-cards");
  list.append(el("h3", "", title));
  if (!rows.length) {
    list.append(el("p", "empty", "No rows."));
    return list;
  }
  rows.forEach((row) => list.append(renderRawCard(row, category, fields)));
  return list;
}

function renderRawCard(row, category, fields) {
  const details = el("details", "tx raw-card");
  const summary = document.createElement("summary");
  const top = el("div", "tx-top");
  top.append(el("div", "merchant", row.description || "No description"));
  top.append(el("div", "amount", money(row.amount)));
  const meta = el("div", "tx-meta");
  meta.append(el("span", "", prettyDate(row.date)));
  meta.append(el("span", "", "Logged by " + (row.logged_by || "—")));
  meta.append(el("span", "tag", category(row)));
  const hint = el("div", "more-hint", "Details");
  summary.append(top, meta, hint);
  const extra = el("div", "raw-extra");
  fields(row).forEach(([label, value]) => {
    const line = el("div", "extra-line");
    const name = el("span", "extra-label", label);
    line.append(name, document.createTextNode(" " + (value || "—")));
    extra.append(line);
  });
  details.append(summary, extra);
  details.addEventListener("toggle", () => {
    hint.textContent = details.open ? "Hide" : "Details";
  });
  return details;
}

function legend() {
  const line = el("p", "colour-key");
  [
    ["green", "Under 60%"],
    ["yellow", "60–79%"],
    ["orange", "80–94%"],
    ["red", "95%+"]
  ].forEach(([key, range]) => {
    const item = el("span", "colour-key-item");
    const dot = el("i", "swatch band-" + key);
    dot.setAttribute("aria-hidden", "true");
    item.append(dot, document.createTextNode(range));
    line.append(item);
  });
  return line;
}

function pageHead(title, subtitle) {
  const head = el("div", "page-head");
  const text = el("div");
  text.append(el("h2", "", title));
  text.append(el("p", "sub", subtitle));
  head.append(text);
  return head;
}

function mark(labeled) {
  const node = el("div", "mark");
  const swatch = el("i");
  swatch.setAttribute("aria-hidden", "true");
  node.append(swatch);
  if (labeled) node.append(document.createTextNode("Household money"));
  return node;
}

function rawTable(title, headers, records) {
  const panel = el("div", "panel scroll raw-desktop");
  panel.append(el("h3", "", title));
  if (!records.length) {
    panel.append(el("p", "empty", "No rows."));
    return panel;
  }
  const table = el("table");
  const caption = el("caption", "sr-only", title);
  table.append(caption);
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
  panel.append(table);
  return panel;
}

function cardField(row) {
  const type = String(row.type || "").toLowerCase();
  if (type === "income") return "No card";
  if (!row.payment_method) return "—";
  const owner = row.payment_owner ? " · " + row.payment_owner + "’s" : "";
  return row.payment_method + owner;
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

function shownPercent(limit) {
  const precise = Number(limit.percent);
  const label = Number(limit.percent_label);
  if (Number.isFinite(label) && bandForPercent(label) === limit.band) return String(limit.percent_label);
  if (!Number.isFinite(precise)) return String(limit.percent_label || "");
  let shown = Math.round(precise);
  if (bandForPercent(shown) !== limit.band) shown = Math.floor(precise);
  if (bandForPercent(shown) !== limit.band) shown = Math.ceil(precise);
  return String(shown);
}

function bandForPercent(value) {
  if (value < 60) return "green";
  if (value < 80) return "yellow";
  if (value < 95) return "orange";
  return "red";
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

function isUp(amount) {
  const value = Number(String(amount).replace(/,/g, ""));
  return Number.isFinite(value) && value >= 0;
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
