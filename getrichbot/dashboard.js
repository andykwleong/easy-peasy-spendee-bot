const ERROR_TEXT = {
  invalid: "Telegram could not confirm who you are. Nothing was opened.",
  expired: "That Telegram confirmation is too old. Try again.",
  not_allowed: "This Telegram account is not allowed to open the dashboard.",
  locked: "This page is locked.",
  sheet: "I could not read the Google Sheet just now. Nothing was changed. Try again in a moment.",
  scope: "Choose one person, or both.",
  cards: "Choose Andy or FX."
};

const state = {
  view: "recent",
  scope: "both",
  cards: "mine",
  missingCategory: false,
  editingId: "",
  savedNote: "",
  pendingDelete: null,
  deleteError: "",
  deleting: false,
  saving: false,
  loading: false,
  payload: null,
  message: "",
  loggedOut: false,
  botUsername: "",
  loadSeq: 0,
  watching: false,
  watchBound: false,
  watchAbort: null,
  wake: null,
  retryDelay: 15000,
  refreshNote: "",
  evalPeriod: "",
  evalOpenId: "",
  evalOpenText: "",
  evalOpenError: "",
  evalOpenGroup: ""
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
      if (await loadDashboard()) watchChanges();
      return;
    }
    state.message = ERROR_TEXT[result.error] || ERROR_TEXT.locked;
    renderLogin();
    return;
  }
  const session = await fetch("/api/session", { credentials: "same-origin" });
  if (session.ok) {
    if (await loadDashboard()) watchChanges();
    return;
  }
  renderLogin();
}

async function loadDashboard(options) {
  const quiet = Boolean(options && options.quiet);
  const seq = ++state.loadSeq;
  if (!quiet) {
    state.loading = true;
    state.message = "";
    state.refreshNote = "";
    renderLoading();
  }
  try {
    return await applyDashboardLoad(quiet, seq);
  } finally {
    if (!quiet) {
      state.loading = false;
      wakeWatch();
    }
  }
}

async function applyDashboardLoad(quiet, seq) {
  let response;
  try {
    response = await fetch(dashboardPath(), {
      credentials: "same-origin",
      headers: telegramInitData() && !state.loggedOut ? { "X-Telegram-Init-Data": telegramInitData() } : {}
    });
  } catch (error) {
    if (seq !== state.loadSeq) return true;
    if (quiet && state.payload && !state.loggedOut) {
      state.refreshNote = "Could not refresh. The list on screen is unchanged.";
      keepScroll(() => render());
      return false;
    }
    if (quiet) return false;
    state.payload = null;
    state.message = ERROR_TEXT.sheet;
    renderMessage(state.message);
    return false;
  }
  const body = await response.json().catch(() => ({ ok: false, error: "sheet" }));
  if (seq !== state.loadSeq) return true;
  if (!response.ok) {
    if (response.status === 401) {
      stopWatching();
      state.payload = null;
      state.refreshNote = "";
      state.message = ERROR_TEXT[body.error] || ERROR_TEXT.locked;
      renderLogin();
      return false;
    }
    if (quiet && state.payload && !state.loggedOut) {
      state.refreshNote = "Could not refresh. The list on screen is unchanged.";
      keepScroll(() => render());
      return false;
    }
    if (quiet) return false;
    state.payload = null;
    state.message = ERROR_TEXT[body.error] || ERROR_TEXT.sheet;
    renderMessage(state.message);
    return false;
  }
  if (quiet && !pageIsReady()) return false;
  state.payload = body;
  state.refreshNote = "";
  if (quiet) keepScroll(() => render());
  else render();
  return true;
}

async function loginWithWidget(user) {
  const result = await postJson("/api/session/widget", user);
  if (!result.ok) {
    state.message = ERROR_TEXT[result.error] || ERROR_TEXT.locked;
    renderLogin();
    return;
  }
  state.loggedOut = false;
  state.scope = "both";
  state.cards = "mine";
  state.missingCategory = false;
  state.editingId = "";
  state.pendingDelete = null;
  state.deleteError = "";
  state.deleting = false;
  state.view = "recent";
  state.saving = false;
  if (await loadDashboard()) watchChanges();
}

async function logout() {
  state.loadSeq += 1;
  stopWatching();
  await fetch("/api/session/logout", { method: "POST", credentials: "same-origin" });
  state.loggedOut = true;
  state.payload = null;
  state.pendingDelete = null;
  state.deleteError = "";
  state.deleting = false;
  state.saving = false;
  state.loading = false;
  state.refreshNote = "";
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
  retry.addEventListener("click", async () => {
    if (await loadDashboard()) watchChanges();
  });
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
      if (await loadDashboard()) watchChanges();
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
  else if (state.view === "eval") wrap.append(renderEval());
  else wrap.append(renderRecent());
  main.append(wrap);
  shell.append(main);
  if (state.pendingDelete) shell.append(renderDeletePopup());
  app.append(shell);
  wakeWatch();
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
    ["eval", "Eval"]
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
  if (state.refreshNote) wrap.append(el("p", "sub", state.refreshNote));
  header.append(wrap);
  return header;
}

function scopeChip(id, label) {
  const chip = el("button", "chip", label);
  chip.type = "button";
  chip.setAttribute("aria-pressed", String(state.scope === id));
  chip.addEventListener("click", () => {
    if (state.scope === id) return;
    state.scope = id;
    loadDashboard();
  });
  return chip;
}

function renderRecent() {
  const section = el("section");
  const head = el("div", "page-head");
  head.append(el("h2", "", "Recent transactions"));
  const filters = el("div", "filters");
  personFilters().forEach((person) => filters.append(scopeChip(person.scope, person.label)));
  filters.append(scopeChip("both", "Both"));
  const missing = el("button", "chip", "No category");
  missing.type = "button";
  missing.setAttribute("aria-pressed", String(state.missingCategory));
  missing.addEventListener("click", () => {
    state.missingCategory = !state.missingCategory;
    loadDashboard();
  });
  filters.append(missing);
  head.append(filters);
  section.append(head);
  if (state.payload.recent_truncated) {
    section.append(el("p", "sub", "Showing the latest " + state.payload.recent_limit + " confirmed rows."));
  }
  if (!state.payload.recent.length) {
    section.append(el("p", "empty", state.missingCategory ? "No rows are missing a category." : "No confirmed rows for this view."));
  }
  state.payload.recent.forEach((row) => section.append(renderTransaction(row)));
  return section;
}

function renderTransaction(row) {
  const pending = expenseCategoryIsBlank(row);
  const article = el("article", pending ? "tx needs-category" : "tx");
  const line = el("div", "tx-line");
  const shop = el("div", "tx-shop");
  shop.append(el("div", "merchant", row.description || "No description"));
  shop.append(el("div", "tx-date", prettyDate(row.date)));
  const mark = messageMark(row);
  if (mark) shop.append(mark);
  const category = el("div", "tx-category");
  const tag = el("span", pending ? "tag needs-category" : "tag", categoryLabel(row));
  category.append(tag);
  const logged = el("div", "tx-logged", "Logged by " + row.logged_by);
  const moneySide = el("div", "tx-money");
  moneySide.append(el("div", "amount", money(row.amount)));
  if (row.kind === "income") moneySide.append(el("div", "tx-card", "No card · income is household"));
  else moneySide.append(el("div", "tx-card", cardLine(row)));
  line.append(shop, category, logged, moneySide);
  article.append(line);
  const actions = el("div", "tx-actions");
  actions.append(deleteButton(row));
  const open = state.editingId === row.id;
  const fix = el("button", "text-btn", open ? "Save" : "Fix tagging");
  fix.type = "button";
  fix.setAttribute("aria-expanded", String(open));
  fix.addEventListener("click", () => {
    if (!open) {
      state.editingId = row.id;
      state.savedNote = "";
      render();
      return;
    }
    const input = fix.closest("article").querySelector('input[aria-label="Amount"]');
    const amount = finishedAmount(input, row);
    state.editingId = "";
    state.savedNote = "";
    if (amount) {
      saveEdit(row, "amount", amount);
      return;
    }
    render();
  });
  actions.append(fix);
  article.append(actions);
  if (open) article.append(renderEditor(row));
  return article;
}

function deleteButton(row) {
  const button = el("button", "text-btn danger-text", "Delete");
  button.type = "button";
  button.setAttribute("aria-label", "Delete " + (row.description || "this row"));
  button.addEventListener("click", () => askDelete(row));
  return button;
}

function deleteSpinner() {
  const spin = el("div", "delete-spin");
  spin.setAttribute("aria-hidden", "true");
  return spin;
}

function askDelete(row) {
  if (state.deleting) return;
  state.pendingDelete = {
    id: row.id,
    source: row.source,
    shop: row.description || "No description",
    amount: row.amount,
    date: row.date
  };
  state.deleteError = "";
  state.deleting = false;
  render();
}

function cancelDelete() {
  if (state.deleting) return;
  state.pendingDelete = null;
  state.deleteError = "";
  render();
}

function renderDeletePopup() {
  const pending = state.pendingDelete;
  const back = el("div", "modal-back");
  const dialog = el("div", "modal");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "delete-title");
  if (state.deleting) dialog.setAttribute("aria-busy", "true");
  const title = el("h3", "", "Delete this row?");
  title.id = "delete-title";
  dialog.append(title);
  dialog.append(el("div", "modal-shop", pending.shop));
  dialog.append(el("div", "modal-amount", money(pending.amount)));
  dialog.append(el("div", "modal-date", prettyDate(pending.date)));
  if (state.deleteError) dialog.append(el("p", "banner", state.deleteError));
  const actions = el("div", "modal-actions");
  const cancel = el("button", "text-btn", "Cancel");
  cancel.type = "button";
  cancel.addEventListener("click", cancelDelete);
  actions.append(cancel);
  if (state.deleting) {
    actions.append(deleteSpinner());
  } else {
    const confirm = el("button", "primary danger", "Delete");
    confirm.type = "button";
    confirm.addEventListener("click", confirmDelete);
    actions.append(confirm);
  }
  dialog.append(actions);
  back.addEventListener("click", (event) => {
    if (event.target === back) cancelDelete();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") cancelDelete();
  });
  back.append(dialog);
  return back;
}

async function confirmDelete() {
  const pending = state.pendingDelete;
  if (!pending || state.deleting) return;
  const seq = ++state.loadSeq;
  state.deleting = true;
  state.deleteError = "";
  render();
  const headers = { "Content-Type": "application/json" };
  if (telegramInitData() && !state.loggedOut) headers["X-Telegram-Init-Data"] = telegramInitData();
  try {
    const response = await fetch("/api/dashboard/delete", {
      method: "POST",
      credentials: "same-origin",
      headers: headers,
      body: JSON.stringify({
        id: pending.id,
        source: pending.source,
        confirm: true,
        scope: state.scope,
        cards: state.cards,
        missing: state.missingCategory
      })
    });
    const body = await response.json().catch(() => ({ ok: false }));
    if (seq !== state.loadSeq) {
      state.deleting = false;
      return;
    }
    if (!response.ok || !body.ok) {
      state.deleting = false;
      state.deleteError = body.message || "I could not delete that row just now. It is still there.";
      render();
      return;
    }
    state.payload = body;
    state.pendingDelete = null;
    state.deleting = false;
    state.deleteError = "";
    if (state.editingId === pending.id) {
      state.editingId = "";
      state.savedNote = "";
    }
    render();
  } catch (error) {
    if (seq !== state.loadSeq) {
      state.deleting = false;
      return;
    }
    state.deleting = false;
    state.deleteError = "I could not delete that row just now. It is still there.";
    render();
  }
}

function renderEditor(row) {
  const box = el("div", "edit-box");
  const fields = fieldsFor(row);
  if (fields.indexOf("category") !== -1) {
    box.append(editSelect("Category", categoryChoices(row), row.category, (value) => {
      if (value !== row.category) saveEdit(row, "category", value);
    }));
  }
  if (fields.indexOf("amount") !== -1) box.append(editAmount(row));
  if (fields.indexOf("card") !== -1) box.append(editCard(row));
  if (fields.indexOf("channel") !== -1) {
    const channel = editChannel(row);
    if (channel) box.append(channel);
  }
  if (state.savedNote) box.append(el("p", "saved-note", state.savedNote));
  return box;
}

function fieldsFor(row) {
  if (row.kind === "card_only") return ["amount", "card", "channel"];
  if (row.kind === "income" || row.kind === "fixed") return ["category", "amount"];
  return ["category", "amount", "card", "channel"];
}

function categoryChoices(row) {
  const choices = state.payload.edit_choices || {};
  if (row.kind === "fixed") return choices.fixed_categories || [];
  if (row.kind === "income" || row.kind === "expense") {
    if (choices.category_picker && choices.category_picker.length) return choices.category_picker;
    return (choices.expense_categories || []).concat(choices.income_categories || []);
  }
  return choices.expense_categories || [];
}

function editCard(row) {
  const cards = (state.payload.edit_choices && state.payload.edit_choices.cards) || [];
  const current = cardKey(row.payment_owner, row.payment_method);
  return editSelect("Card", cards.map((card) => ({
    value: cardKey(card.owner, card.name),
    label: card.owner + " · " + card.name
  })), current, (value) => {
    const picked = cards.find((card) => cardKey(card.owner, card.name) === value);
    if (!picked || (picked.name === row.payment_method && picked.owner === row.payment_owner)) return;
    saveEdit(row, "card", picked.name, picked.owner);
  });
}

function editChannel(row) {
  const cards = (state.payload.edit_choices && state.payload.edit_choices.cards) || [];
  const card = cards.find((item) => item.name === row.payment_method && item.owner === row.payment_owner);
  const channels = card ? card.channels : [];
  if (!channels.length) return null;
  if (channels.length === 1) return editField("Channel", el("span", "", channels[0]));
  return editSelect("Channel", channels, row.payment_channel, (value) => {
    if (value && value !== row.payment_channel) saveEdit(row, "channel", value);
  });
}

function editAmount(row) {
  const input = document.createElement("input");
  input.type = "text";
  input.inputMode = "decimal";
  input.value = row.amount;
  input.setAttribute("aria-label", "Amount");
  const commit = () => {
    const cleaned = input.value.trim().replace(/[$,]/g, "");
    if (cleaned === String(row.amount) || Number(cleaned).toFixed(2) === Number(row.amount).toFixed(2)) return;
    if (!/^\d+(?:\.\d{1,2})?$/.test(cleaned) || Number(cleaned) <= 0) {
      state.savedNote = "Finish the amount, such as 23.20. Nothing was saved yet.";
      render();
      return;
    }
    saveEdit(row, "amount", cleaned);
  };
  input.addEventListener("change", commit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  return editField("Amount", input);
}

function editSelect(label, choices, current, onPick) {
  const select = document.createElement("select");
  select.setAttribute("aria-label", label);
  const items = choices.map((choice) => (typeof choice === "string" ? { value: choice, label: choice } : choice));
  if (!items.some((item) => item.value === current)) {
    const blank = document.createElement("option");
    blank.value = "";
    blank.textContent = "Choose";
    blank.selected = true;
    select.append(blank);
  }
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = item.label;
    if (item.value === current) option.selected = true;
    select.append(option);
  });
  select.addEventListener("change", () => {
    if (select.value) onPick(select.value);
  });
  return editField(label, select);
}

function editField(label, control) {
  const field = el("label", "edit-row");
  field.append(el("span", "", label), control);
  return field;
}

function cardKey(owner, name) {
  return (owner || "") + "\n" + (name || "");
}

async function saveEdit(row, field, value, owner) {
  const seq = ++state.loadSeq;
  state.saving = true;
  const payload = {
    id: row.id,
    source: row.source,
    field: field,
    value: value,
    scope: state.scope,
    cards: state.cards,
    missing: state.missingCategory
  };
  if (owner) payload.owner = owner;
  const headers = { "Content-Type": "application/json" };
  if (telegramInitData() && !state.loggedOut) headers["X-Telegram-Init-Data"] = telegramInitData();
  try {
    const response = await fetch("/api/dashboard/edit", {
      method: "POST",
      credentials: "same-origin",
      headers: headers,
      body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (seq !== state.loadSeq) return;
    if (!response.ok || !body.ok) {
      state.savedNote = body.message || ERROR_TEXT.sheet;
      render();
      return;
    }
    state.payload = body;
    state.savedNote = body.message || "Saved.";
    render();
  } catch (error) {
    if (seq !== state.loadSeq) return;
    state.savedNote = ERROR_TEXT.sheet;
    render();
  } finally {
    state.saving = false;
    wakeWatch();
  }
}

function renderCards() {
  const section = el("section");
  const cards = state.payload.cards;
  const head = el("div", "page-head");
  head.append(el("h2", "", "Card summary"));
  const people = personFilters();
  if (people.length > 1) {
    const filters = el("div", "filters");
    people.forEach((person) => {
      const chip = el("button", "chip", person.label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(cardsPersonPressed(person)));
      chip.addEventListener("click", () => {
        if (cardsPersonPressed(person)) return;
        state.cards = person.scope;
        loadDashboard();
      });
      filters.append(chip);
    });
    head.append(filters);
  }
  section.append(head);
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
    article.append(block);
  });
  return article;
}

function renderMonth() {
  const section = el("section");
  const head = el("div", "page-head");
  head.append(el("h2", "", "Monthly summary"));
  section.append(head);
  const months = state.payload.months;
  const panel = el("div", "panel scroll");
  panel.append(el("h3", "", "From the raw rows"));
  const table = el("table");
  const tableHead = el("thead");
  const headRow = el("tr");
  headRow.append(el("th", "", ""));
  months.forEach((month) => headRow.append(el("th", "num", month.label)));
  tableHead.append(headRow);
  table.append(tableHead);
  const body = el("tbody");
  monthRows(months).forEach((row) => body.append(row));
  table.append(body);
  panel.append(table);
  section.append(panel);
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
  const section = el("section", "raw-page");
  const head = el("div", "page-head");
  head.append(el("h2", "", "Raw entries"));
  section.append(head);
  const frame = el("div", "raw-frame");
  bindRawPan(frame);
  if (state.payload.raw_expenses_truncated) {
    frame.append(el("p", "sub", "Showing the latest " + state.payload.raw_expenses_limit + " raw expense rows."));
  }
  frame.append(rawTable(
    "Raw Expenses",
    ["Entry ID", "Date", "Logged by", "Amount", "Category", "Description", "Payment owner", "Payment method", "Channel", "Type", "Status"],
    state.payload.raw_expenses.map((row) => ({
      cells: [
        row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.category, row.description,
        row.payment_owner || "—", row.payment_method || "—", row.payment_channel || "—", row.type, row.status
      ],
      deleteTarget: rawDeleteTarget(row, "expense"),
      needsCategory: expenseCategoryIsBlank(row)
    }))
  ));
  if (state.payload.card_usage_truncated) {
    frame.append(el("p", "sub", "Showing the latest " + state.payload.card_usage_limit + " card-only rows."));
  }
  frame.append(rawTable(
    "Card Usage",
    ["Entry ID", "Date", "Logged by", "Amount", "Payment owner", "Payment method", "Channel", "Description", "Type", "Status"],
    state.payload.card_usage.map((row) => ({
      cells: [
        row.id, prettyDate(row.date), row.logged_by, money(row.amount), row.payment_owner || "—",
        row.payment_method || "—", row.payment_channel || "—", row.description, row.type, row.status
      ],
      deleteTarget: rawDeleteTarget(row, "card_usage")
    }))
  ));
  section.append(frame);
  return section;
}

function personFilters() {
  const people = state.payload && state.payload.person_filters;
  return Array.isArray(people) ? people : [];
}

function cardsPersonPressed(person) {
  if (state.cards === person.scope) return true;
  if (state.cards === "mine" && person.scope === state.payload.viewer_label) return true;
  const other = state.payload.cards && state.payload.cards.other_label;
  return state.cards === "other" && person.scope === other;
}

function messageMark(row) {
  if (row.message_status !== "Message sent" && row.message_status !== "message fail") return null;
  const mark = el("div", "msg-mark");
  const dot = el("i", row.message_status === "Message sent" ? "dot dot-green" : "dot dot-red");
  dot.setAttribute("aria-hidden", "true");
  mark.append(dot, document.createTextNode(row.message_status));
  return mark;
}

function renderEval() {
  const section = el("section");
  const info = (state.payload && state.payload.agent_eval) || {};
  const period = selectedEvalPeriod(info);
  const head = el("div", "page-head");
  const text = el("div");
  text.append(el("h2", "", "Eval"));
  text.append(el("p", "sub", "Mail and categories use the day the mail was handled. Group notes use the expense date."));
  head.append(text);
  section.append(head);
  section.append(evalPeriodChips(info, period));
  section.append(renderEmailEval(info, period));
  section.append(renderCategoryEval(info, period));
  section.append(renderGroupEval(info, period));
  return section;
}

function selectedEvalPeriod(info) {
  if (state.evalPeriod) return state.evalPeriod;
  const today = String(info.today || "");
  return today.length >= 7 ? today.slice(0, 7) : "all";
}

function evalPeriodChips(info, period) {
  const filters = el("div", "filters eval-periods");
  evalMonths(info).forEach((month) => {
    filters.append(evalPeriodChip(month, monthLabel(month), period));
  });
  filters.append(evalPeriodChip("all", "All time", period));
  return filters;
}

function evalPeriodChip(id, label, period) {
  const chip = el("button", "chip", label);
  chip.type = "button";
  chip.setAttribute("aria-pressed", String(period === id));
  chip.addEventListener("click", () => {
    if (state.evalPeriod === id) return;
    state.evalPeriod = id;
    state.evalOpenId = "";
    state.evalOpenText = "";
    state.evalOpenError = "";
    state.evalOpenGroup = "";
    render();
  });
  return chip;
}

function evalMonths(info) {
  const months = new Set();
  const today = String(info.today || "");
  if (today.length >= 7) months.add(today.slice(0, 7));
  ["emails", "purchases", "group_messages"].forEach((key) => {
    (info[key] || []).forEach((row) => {
      const month = String(row.date || "").slice(0, 7);
      if (/^\d{4}-\d{2}$/.test(month)) months.add(month);
    });
  });
  return Array.from(months).sort().reverse();
}

function monthLabel(month) {
  const names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const parts = String(month || "").split("-");
  const name = names[Number(parts[1]) - 1];
  if (!name || !parts[0]) return month;
  return name + " " + parts[0];
}

function inEvalPeriod(dateText, period) {
  if (period === "all") return true;
  return String(dateText || "").slice(0, 7) === period;
}

function periodPhrase(period) {
  return period === "all" ? "all time" : monthLabel(period);
}

function wholePercent(part, whole) {
  if (!whole) return null;
  return Math.floor((part * 100 + Math.floor(whole / 2)) / whole);
}

function evalHealthBand(percent) {
  if (percent >= 95) return "green";
  if (percent >= 80) return "orange";
  if (percent >= 60) return "yellow";
  return "red";
}

function evalCount(percent) {
  return el("p", "eval-count tone-" + evalHealthBand(percent), percent + "%");
}

function renderEmailEval(info, period) {
  const block = el("div", "eval-block");
  block.append(el("h3", "", "Email successfully logged"));
  const rows = (info.emails || []).filter((row) => inEvalPeriod(row.date, period));
  const logged = rows.filter((row) => row.result === "pass");
  const missed = rows.filter((row) => row.result !== "pass");
  const percent = wholePercent(logged.length, rows.length);
  if (percent === null) {
    block.append(el("p", "", "No mail for " + periodPhrase(period) + "."));
    return block;
  }
  block.append(evalCount(percent));
  block.append(el("p", "", "logged"));
  block.append(el("p", "", logged.length + " logged"));
  block.append(el("p", "", missed.length + " not logged"));
  if (!missed.length) {
    block.append(el("p", "sub", "Every mail in this view was logged."));
    return block;
  }
  const list = el("div", "eval-list");
  missed.forEach((row) => list.append(renderFailedMail(row)));
  block.append(list);
  return block;
}

function renderFailedMail(row) {
  const open = state.evalOpenId === row.message_id;
  const item = el("button", "eval-card" + (open ? " open" : ""));
  item.type = "button";
  item.setAttribute("aria-expanded", String(open));
  item.addEventListener("click", () => toggleFailedMail(row.message_id));
  const top = el("div", "eval-card-top");
  const titles = el("div");
  titles.append(el("p", "eval-card-title", row.subject || "(no subject)"));
  titles.append(el("p", "sub", shortDate(row.date) + " · " + (row.sender || "Unknown sender")));
  top.append(titles);
  top.append(el("p", "sub eval-card-aside", row.reason || "Not logged"));
  item.append(top);
  if (open) item.append(failedMailBody());
  return item;
}

function failedMailBody() {
  const note = el("pre", "eval-mail");
  if (state.evalOpenError) note.textContent = state.evalOpenError;
  else if (state.evalOpenText) note.textContent = state.evalOpenText;
  else note.textContent = "Reading that mail...";
  return note;
}

function toggleFailedMail(messageId) {
  if (state.evalOpenId === messageId) {
    state.evalOpenId = "";
    state.evalOpenText = "";
    state.evalOpenError = "";
    render();
    return;
  }
  state.evalOpenId = messageId;
  state.evalOpenText = "";
  state.evalOpenError = "";
  render();
  const headers = telegramInitData() && !state.loggedOut ? { "X-Telegram-Init-Data": telegramInitData() } : {};
  fetch("/api/dashboard/eval-mail?id=" + encodeURIComponent(messageId), {
    credentials: "same-origin",
    headers: headers
  }).then((response) => response.json().catch(() => ({ ok: false }))).then((body) => {
    if (state.evalOpenId !== messageId) return;
    if (!body || !body.ok) state.evalOpenError = (body && body.message) || "I could not read that mail.";
    else state.evalOpenText = body.body || "That mail had no text.";
    render();
  }).catch(() => {
    if (state.evalOpenId !== messageId) return;
    state.evalOpenError = "I could not read that mail.";
    render();
  });
}

function renderCategoryEval(info, period) {
  const block = el("div", "eval-block");
  block.append(el("h3", "", "Categories successfully matched"));
  const rows = (info.purchases || []).filter((row) => inEvalPeriod(row.date, period));
  const changed = rows.filter((row) => row.changed);
  const kept = rows.length - changed.length;
  const percent = wholePercent(kept, rows.length);
  if (percent === null) {
    block.append(el("p", "", "No email purchases for " + periodPhrase(period) + "."));
    return block;
  }
  block.append(evalCount(percent));
  block.append(el("p", "", "unchanged after logging"));
  block.append(el("p", "", kept + " left as logged"));
  block.append(el("p", "", changed.length + " changed later"));
  if (!changed.length) {
    block.append(el("p", "sub", "No email purchase was given a different category after it was logged."));
    return block;
  }
  const list = el("div", "eval-list");
  changed.forEach((row) => {
    const item = el("div", "eval-card");
    const shop = row.shop ? row.shop + ": " : "";
    item.append(el("p", "eval-card-title", shop + (row.first_category || "blank") + ", then " + (row.final_category || "blank")));
    item.append(el("p", "sub", shortDate(row.date)));
    list.append(item);
  });
  block.append(list);
  return block;
}

function renderGroupEval(info, period) {
  const block = el("div", "eval-block");
  block.append(el("h3", "", "Telegram message successfully sent"));
  const rows = (info.group_messages || []).filter((row) => inEvalPeriod(row.date, period));
  const sent = rows.filter((row) => row.sent);
  const missed = rows.filter((row) => !row.sent);
  const percent = wholePercent(sent.length, rows.length);
  if (percent === null) {
    block.append(el("p", "", "No group notes for " + periodPhrase(period) + "."));
    return block;
  }
  block.append(evalCount(percent));
  block.append(el("p", "", "sent"));
  block.append(el("p", "", sent.length + " sent"));
  block.append(el("p", "", missed.length + " not sent"));
  if (!missed.length) {
    block.append(el("p", "sub", "Telegram accepted every group note in this view."));
    return block;
  }
  const list = el("div", "eval-list");
  missed.forEach((row, index) => list.append(renderFailedGroup(row, index)));
  block.append(list);
  return block;
}

function renderFailedGroup(row, index) {
  const key = [index, row.date, row.shop, row.amount, row.logged_by].join("|");
  const open = state.evalOpenGroup === key;
  const item = el("button", "eval-card" + (open ? " open" : ""));
  item.type = "button";
  item.setAttribute("aria-expanded", String(open));
  item.addEventListener("click", () => {
    state.evalOpenGroup = open ? "" : key;
    render();
  });
  const top = el("div", "eval-card-top");
  const titles = el("div");
  titles.append(el("p", "eval-card-title", (row.shop || "Unknown shop") + " · $" + row.amount));
  titles.append(el("p", "sub", shortDate(row.date) + " · logged by " + (row.logged_by || "unknown")));
  top.append(titles);
  top.append(el("p", "sub eval-card-aside", "message fail"));
  item.append(top);
  if (open) {
    item.append(el("p", "eval-note", "The purchase is on the sheet. Telegram did not accept the group note."));
  }
  return item;
}

function shortDate(iso) {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) return String(iso || "");
  return Number(match[3]) + " " + names[Number(match[2]) - 1] + " " + match[1];
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

function rawDeleteTarget(row, source) {
  return {
    id: row.id,
    source: source,
    description: row.description || "No description",
    amount: row.amount,
    date: row.date
  };
}

function rawTable(title, headers, records) {
  const panel = el("div", "panel raw-block");
  panel.append(el("h3", "", title));
  if (!records.length) {
    panel.append(el("p", "empty", "No rows."));
    return panel;
  }
  const table = el("table");
  const head = el("tr");
  headers.forEach((header, index) => head.append(el("th", index === 3 ? "num" : "", header)));
  head.append(el("th", "", ""));
  const thead = el("thead");
  thead.append(head);
  table.append(thead);
  const body = el("tbody");
  records.forEach((record) => {
    const row = el("tr", record.needsCategory ? "needs-category" : "");
    record.cells.forEach((value, index) => row.append(el("td", index === 3 ? "num" : "", value)));
    const action = el("td");
    if (record.deleteTarget) action.append(deleteButton(record.deleteTarget));
    row.append(action);
    body.append(row);
  });
  table.append(body);
  panel.append(table);
  return panel;
}

function watchChanges() {
  if (state.watching) return;
  if (!state.payload || typeof state.payload.change_mark !== "string" || !state.payload.change_mark) return;
  state.watching = true;
  state.retryDelay = 15000;
  if (!state.watchBound) {
    state.watchBound = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && state.watchAbort) {
        state.watchAbort.abort();
        state.watchAbort = null;
      }
      wakeWatch();
    });
    document.addEventListener("focusout", () => wakeWatch());
  }
  runWatch();
}

function stopWatching() {
  state.watching = false;
  if (state.watchAbort) {
    state.watchAbort.abort();
    state.watchAbort = null;
  }
  wakeWatch();
}

function wakeWatch() {
  const wake = state.wake;
  state.wake = null;
  if (typeof wake === "function") wake();
}

async function runWatch() {
  while (state.watching) {
    await untilReady();
    if (!state.watching) return;
    if (!state.payload || typeof state.payload.change_mark !== "string" || !state.payload.change_mark) {
      stopWatching();
      return;
    }
    const since = state.payload.change_mark;
    const result = await waitForMark(since);
    if (!state.watching) return;
    if (!result || result.aborted) continue;
    if (result.failed) {
      await pause(state.retryDelay);
      if (!state.watching) return;
      state.retryDelay = Math.min(state.retryDelay * 2, 120000);
      continue;
    }
    if (!state.payload || result.mark === state.payload.change_mark) continue;
    await untilReady();
    if (!state.watching || !state.payload) return;
    if (result.mark === state.payload.change_mark) continue;
    const applied = await loadDashboard({ quiet: true });
    if (!state.watching) return;
    if (applied) {
      state.retryDelay = 15000;
      continue;
    }
    if (!pageIsReady()) continue;
    await pause(state.retryDelay);
    if (!state.watching) return;
    state.retryDelay = Math.min(state.retryDelay * 2, 120000);
  }
}

function pageIsReady() {
  if (document.hidden || state.loggedOut || !state.payload) return false;
  if (state.editingId || state.pendingDelete || state.deleting || state.saving || state.loading) return false;
  const active = document.activeElement;
  if (!active || !active.closest || !active.closest("#app")) return true;
  return active.tagName !== "INPUT" && active.tagName !== "SELECT";
}

async function untilReady() {
  while (state.watching && !pageIsReady()) {
    if (state.watchAbort) {
      state.watchAbort.abort();
      state.watchAbort = null;
    }
    await new Promise((resolve) => {
      state.wake = resolve;
    });
  }
}

async function waitForMark(since) {
  if (state.watchAbort) state.watchAbort.abort();
  const abort = new AbortController();
  state.watchAbort = abort;
  try {
    const response = await fetch("/api/dashboard/changes?since=" + encodeURIComponent(since), {
      credentials: "same-origin",
      signal: abort.signal,
      headers: telegramInitData() && !state.loggedOut ? { "X-Telegram-Init-Data": telegramInitData() } : {}
    });
    if (!state.watching || abort.signal.aborted) return { aborted: true };
    if (response.status === 401) {
      stopWatching();
      state.payload = null;
      state.refreshNote = "";
      state.message = ERROR_TEXT.locked;
      renderLogin();
      return { aborted: true };
    }
    if (response.status === 503) {
      stopWatching();
      return { aborted: true };
    }
    if (response.status === 429 || !response.ok) return { failed: true };
    const body = await response.json().catch(() => null);
    if (!body || !body.ok || typeof body.mark !== "string") return { failed: true };
    return { mark: body.mark };
  } catch (error) {
    if (error && error.name === "AbortError") return { aborted: true };
    return { failed: true };
  } finally {
    if (state.watchAbort === abort) state.watchAbort = null;
  }
}

function bindRawPan(frame) {
  if (!state.rawPanBound) {
    state.rawPanBound = true;
    window.addEventListener("mousemove", moveRawPan);
    window.addEventListener("mouseup", endRawPan);
  }
  frame.addEventListener("mousedown", startRawPan);
}

function startRawPan(event) {
  if (event.button !== 0) return;
  if (event.target.closest("button, a, input, select, textarea, label")) return;
  state.rawPan = {
    startX: event.clientX,
    startY: event.clientY,
    startLeft: event.currentTarget.scrollLeft,
    startTop: window.scrollY
  };
  event.currentTarget.classList.add("is-panning");
  event.preventDefault();
}

function moveRawPan(event) {
  if (!state.rawPan) return;
  const frame = document.querySelector(".raw-frame");
  if (!frame) return;
  frame.scrollLeft = state.rawPan.startLeft - (event.clientX - state.rawPan.startX);
  window.scrollTo(window.scrollX, state.rawPan.startTop - (event.clientY - state.rawPan.startY));
}

function endRawPan() {
  if (!state.rawPan) return;
  state.rawPan = null;
  const frame = document.querySelector(".raw-frame");
  if (frame) frame.classList.remove("is-panning");
}

function keepScroll(paint) {
  const frame = document.querySelector(".raw-frame");
  const top = frame ? frame.scrollTop : 0;
  const left = frame ? frame.scrollLeft : 0;
  const x = window.scrollX;
  const y = window.scrollY;
  paint();
  window.scrollTo(x, y);
  const next = document.querySelector(".raw-frame");
  if (next) {
    next.scrollTop = top;
    next.scrollLeft = left;
  }
}

function pause(ms) {
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      if (state.wake === finish) state.wake = null;
      resolve();
    }, ms);
    function finish() {
      window.clearTimeout(timer);
      resolve();
    }
    state.wake = finish;
  });
}

function dashboardPath() {
  let path = "/api/dashboard?scope=" + encodeURIComponent(state.scope) + "&cards=" + encodeURIComponent(state.cards);
  if (state.missingCategory) path += "&missing=1";
  return path;
}

function expenseCategoryIsBlank(row) {
  if (String(row.category || "").trim()) return false;
  if (row.kind) return row.kind === "expense";
  const type = String(row.type || "Expense").toLowerCase();
  const status = String(row.status || "Confirmed").toLowerCase();
  return type === "expense" && status === "confirmed";
}

function categoryLabel(row) {
  if (row.kind === "card_only") return "Card only · not an expense";
  if (expenseCategoryIsBlank(row)) return "No category";
  return row.category || row.kind;
}

function finishedAmount(input, row) {
  if (!input) return "";
  const cleaned = input.value.trim().replace(/[$,]/g, "");
  if (!cleaned || cleaned === String(row.amount) || Number(cleaned).toFixed(2) === Number(row.amount).toFixed(2)) return "";
  if (!/^\d+(?:\.\d{1,2})?$/.test(cleaned) || Number(cleaned) <= 0) return "";
  return cleaned;
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
