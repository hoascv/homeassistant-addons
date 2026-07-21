const ICONS = { egg: "🥚", cleaning: "🧹", feeding: "🌾", sale: "💰", expense: "🧾", used: "🍳" };
const TITLES = {
  egg: "Log Eggs",
  cleaning: "Log Cleaning",
  feeding: "Log Feeding",
  sale: "Log Sale",
  expense: "Log Expense",
  used: "Log Used",
};

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

const sheetBackdrop = document.getElementById("sheet-backdrop");
const sheetTitle = document.getElementById("sheet-title");
const sheetFields = document.getElementById("sheet-fields");
const sheetForm = document.getElementById("sheet-form");
const sheetCancel = document.getElementById("sheet-cancel");
const historyFilter = document.getElementById("history-filter");
const historyList = document.getElementById("history-list");

let currentType = null;
let currentEntryId = null;
let entriesCache = {};

const CURRENT_DATE = new Date();
let financeYear = CURRENT_DATE.getFullYear();
let financeMonth = CURRENT_DATE.getMonth() + 1; // 1-12

function fmtTime(iso) {
  if (!iso) return "Never";
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `Today ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + ` ${time}`;
}

function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function fmtMoney(value) {
  const cfg = window.CURRENCY || { symbol: "$", position: "prefix", decimals: 2 };
  const amount = value === null || value === undefined ? 0 : Number(value);
  const formatted = amount.toFixed(cfg.decimals);
  return cfg.position === "suffix" ? `${formatted} ${cfg.symbol}` : `${cfg.symbol}${formatted}`;
}

async function loadSummary() {
  const monthParam = `${financeYear}-${String(financeMonth).padStart(2, "0")}`;
  const res = await fetch(`api/summary?month=${monthParam}`);
  const data = await res.json();
  document.getElementById("stat-eggs-today").textContent = data.eggs_today;
  document.getElementById("stat-eggs-week").textContent = data.eggs_week;
  document.getElementById("stat-last-cleaning").textContent = fmtTime(data.last_cleaning);
  document.getElementById("stat-last-feeding").textContent = fmtTime(data.last_feeding);
  document.getElementById("stat-eggs-available").textContent = data.eggs_available;
  document.getElementById("stat-revenue-month").textContent = fmtMoney(data.revenue_month);
  document.getElementById("stat-cost-month").textContent = fmtMoney(data.cost_month);

  const netEl = document.getElementById("stat-net-month");
  netEl.textContent = fmtMoney(data.net_month);
  netEl.classList.toggle("stat-positive", data.net_month >= 0);
  netEl.classList.toggle("stat-negative", data.net_month < 0);

  document.getElementById("stat-revenue-total").textContent = fmtMoney(data.revenue_total);
  document.getElementById("stat-cost-total").textContent = fmtMoney(data.cost_total);

  const netTotalEl = document.getElementById("stat-net-total");
  netTotalEl.textContent = fmtMoney(data.net_total);
  netTotalEl.classList.toggle("stat-positive", data.net_total >= 0);
  netTotalEl.classList.toggle("stat-negative", data.net_total < 0);

  document.getElementById("finance-month-label").textContent =
    `${MONTH_NAMES[financeMonth - 1]} ${financeYear}`;

  const isCurrentMonth =
    financeYear === CURRENT_DATE.getFullYear() && financeMonth === CURRENT_DATE.getMonth() + 1;
  document.getElementById("finance-next-month").disabled = isCurrentMonth;
}

async function loadHistory() {
  const type = historyFilter.value;
  const url = type ? `api/entries?type=${type}` : "api/entries";
  const res = await fetch(url);
  const entries = await res.json();

  entriesCache = {};
  historyList.innerHTML = "";
  if (entries.length === 0) {
    historyList.innerHTML = '<li class="empty-state">No entries yet</li>';
    return;
  }

  for (const entry of entries) {
    entriesCache[entry.id] = entry;

    const li = document.createElement("li");
    li.className = "history-item";
    li.dataset.id = entry.id;

    let title;
    if (entry.type === "egg") title = `${entry.count ?? 1} egg${entry.count === 1 ? "" : "s"} collected`;
    else if (entry.type === "cleaning") title = "Coop cleaned";
    else if (entry.type === "feeding")
      title = `Fed${entry.food_type ? " " + entry.food_type : ""}${entry.amount ? " · " + entry.amount : ""}${entry.container_empty ? " · container was empty" : ""}`;
    else if (entry.type === "sale")
      title = `${entry.count ?? 1} egg${entry.count === 1 ? "" : "s"} sold${entry.price != null ? " · " + fmtMoney(entry.price) : ""}`;
    else if (entry.type === "expense")
      title = `${entry.category || "Expense"}${entry.cost != null ? " · " + fmtMoney(entry.cost) : ""}`;
    else title = `${entry.count ?? 1} egg${entry.count === 1 ? "" : "s"} used`;

    li.innerHTML = `
      <span class="icon">${ICONS[entry.type]}</span>
      <div class="details">
        <div class="title">${title}</div>
        <div class="meta">${fmtTime(entry.ts)}${entry.notes ? " · " + escapeHtml(entry.notes) : ""}</div>
      </div>
      <button class="delete-btn" data-id="${entry.id}" aria-label="Delete">✕</button>
    `;
    historyList.appendChild(li);
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function dateFieldHtml(value) {
  return `
    <div class="field">
      <label>Date &amp; time</label>
      <input type="datetime-local" name="ts" value="${value}" required>
    </div>
  `;
}

async function updateFeedingStatsHint(foodType) {
  const hintEl = document.getElementById("feeding-stats-hint");
  if (!hintEl) return;

  const trimmed = (foodType || "").trim();
  if (!trimmed) {
    hintEl.textContent = "";
    return;
  }

  try {
    const res = await fetch(`api/feeding-stats?food_type=${encodeURIComponent(trimmed)}`);
    const data = await res.json();
    const daysSince = data.days_since_last_empty != null ? Math.round(data.days_since_last_empty) : null;

    if (data.empty_count === 0) {
      hintEl.textContent = `No "container was empty" history yet for ${trimmed}.`;
    } else if (data.avg_days_between_empty == null) {
      hintEl.textContent = `${trimmed}: container last emptied ${daysSince} day${daysSince === 1 ? "" : "s"} ago. Log one more empty container to see an average.`;
    } else {
      hintEl.textContent = `${trimmed}: avg ${data.avg_days_between_empty} days between refills · last emptied ${daysSince} day${daysSince === 1 ? "" : "s"} ago.`;
    }
  } catch (err) {
    hintEl.textContent = "";
  }
}

async function prefillLastFoodType(inputEl) {
  try {
    const res = await fetch("api/entries?type=feeding&limit=1");
    const entries = await res.json();
    if (entries.length && entries[0].food_type) {
      inputEl.value = entries[0].food_type;
    }
  } catch (err) {
    // leave the field blank
  }
  updateFeedingStatsHint(inputEl.value);
}

function openSheet(type, entry = null) {
  currentType = type;
  currentEntryId = entry ? entry.id : null;
  sheetTitle.textContent = entry ? `Edit ${TITLES[type].replace("Log ", "")}` : TITLES[type];
  sheetFields.innerHTML = "";

  const tsValue = toLocalInputValue(entry ? new Date(entry.ts) : new Date());

  if (type === "egg") {
    const initialCount = entry ? entry.count ?? 1 : 1;
    sheetFields.innerHTML = `
      <div class="field">
        <label>Eggs collected</label>
        <div class="stepper">
          <button type="button" id="dec">−</button>
          <span id="count-value">${initialCount}</span>
          <button type="button" id="inc">+</button>
        </div>
      </div>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes" placeholder="e.g. one cracked">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
    let count = initialCount;
    const countValue = document.getElementById("count-value");
    document.getElementById("dec").addEventListener("click", () => {
      count = Math.max(0, count - 1);
      countValue.textContent = count;
    });
    document.getElementById("inc").addEventListener("click", () => {
      count += 1;
      countValue.textContent = count;
    });
  } else if (type === "cleaning") {
    sheetFields.innerHTML = `
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes" placeholder="e.g. full bedding change">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
  } else if (type === "feeding") {
    sheetFields.innerHTML = `
      <div class="field">
        <label>Food type</label>
        <input type="text" name="food_type" id="feeding-food-type" placeholder="e.g. layer feed, scratch grains" value="${entry ? entry.food_type ?? "" : ""}">
      </div>
      <p class="feeding-stats-hint" id="feeding-stats-hint"></p>
      <div class="field">
        <label>Amount (optional)</label>
        <input type="text" name="amount" placeholder="e.g. 2 cups" value="${entry ? entry.amount ?? "" : ""}">
      </div>
      <label class="field-checkbox">
        <input type="checkbox" name="container_empty" id="feeding-container-empty" ${entry && entry.container_empty ? "checked" : ""}>
        Container was empty
      </label>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;

    const foodTypeInput = document.getElementById("feeding-food-type");
    let statsDebounce;
    foodTypeInput.addEventListener("input", () => {
      clearTimeout(statsDebounce);
      statsDebounce = setTimeout(() => updateFeedingStatsHint(foodTypeInput.value), 400);
    });

    if (entry) {
      updateFeedingStatsHint(foodTypeInput.value);
    } else {
      prefillLastFoodType(foodTypeInput);
    }
  } else if (type === "sale") {
    const initialCount = entry ? entry.count ?? 1 : 1;
    sheetFields.innerHTML = `
      <div class="field">
        <label>Eggs sold</label>
        <div class="stepper">
          <button type="button" id="dec">−</button>
          <span id="count-value">${initialCount}</span>
          <button type="button" id="inc">+</button>
        </div>
      </div>
      <div class="field">
        <label>Total price received</label>
        <input type="number" step="0.01" min="0" inputmode="decimal" name="price" placeholder="e.g. 6.00" value="${entry && entry.price != null ? entry.price : ""}">
      </div>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes" placeholder="e.g. sold to neighbor">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
    let count = initialCount;
    const countValue = document.getElementById("count-value");
    document.getElementById("dec").addEventListener("click", () => {
      count = Math.max(0, count - 1);
      countValue.textContent = count;
    });
    document.getElementById("inc").addEventListener("click", () => {
      count += 1;
      countValue.textContent = count;
    });
  } else if (type === "expense") {
    sheetFields.innerHTML = `
      <div class="field">
        <label>Category</label>
        <input type="text" name="category" placeholder="e.g. Food, Bedding, Medical" value="${entry ? entry.category ?? "" : ""}" list="expense-categories">
        <datalist id="expense-categories">
          <option value="Food"></option>
          <option value="Material"></option>
          <option value="Medical"></option>
          <option value="Other"></option>
        </datalist>
      </div>
      <div class="field">
        <label>Amount spent</label>
        <input type="number" step="0.01" min="0" inputmode="decimal" name="cost" placeholder="e.g. 24.99" value="${entry && entry.cost != null ? entry.cost : ""}">
      </div>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
  } else if (type === "used") {
    const initialCount = entry ? entry.count ?? 1 : 1;
    sheetFields.innerHTML = `
      <div class="field">
        <label>Eggs used</label>
        <div class="stepper">
          <button type="button" id="dec">−</button>
          <span id="count-value">${initialCount}</span>
          <button type="button" id="inc">+</button>
        </div>
      </div>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes" placeholder="e.g. baking">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
    let count = initialCount;
    const countValue = document.getElementById("count-value");
    document.getElementById("dec").addEventListener("click", () => {
      count = Math.max(0, count - 1);
      countValue.textContent = count;
    });
    document.getElementById("inc").addEventListener("click", () => {
      count += 1;
      countValue.textContent = count;
    });
  }

  sheetBackdrop.classList.add("open");
}

function closeSheet() {
  sheetBackdrop.classList.remove("open");
  currentType = null;
  currentEntryId = null;
}

document.querySelectorAll(".action-btn").forEach((btn) => {
  btn.addEventListener("click", () => openSheet(btn.dataset.action));
});

sheetCancel.addEventListener("click", closeSheet);
sheetBackdrop.addEventListener("click", (e) => {
  if (e.target === sheetBackdrop) closeSheet();
});

sheetForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = { type: currentType, ts: sheetForm.ts.value };

  if (currentType === "egg") {
    payload.count = parseInt(document.getElementById("count-value").textContent, 10);
    payload.notes = sheetForm.notes.value || null;
  } else if (currentType === "cleaning") {
    payload.notes = sheetForm.notes.value || null;
  } else if (currentType === "feeding") {
    payload.food_type = sheetForm.food_type.value || null;
    payload.amount = sheetForm.amount.value || null;
    payload.notes = sheetForm.notes.value || null;
    payload.container_empty = document.getElementById("feeding-container-empty").checked;
  } else if (currentType === "sale") {
    payload.count = parseInt(document.getElementById("count-value").textContent, 10);
    payload.price = sheetForm.price.value === "" ? null : parseFloat(sheetForm.price.value);
    payload.notes = sheetForm.notes.value || null;
  } else if (currentType === "expense") {
    payload.category = sheetForm.category.value || null;
    payload.cost = sheetForm.cost.value === "" ? null : parseFloat(sheetForm.cost.value);
    payload.notes = sheetForm.notes.value || null;
  } else if (currentType === "used") {
    payload.count = parseInt(document.getElementById("count-value").textContent, 10);
    payload.notes = sheetForm.notes.value || null;
  }

  const saveBtn = sheetForm.querySelector('button[type="submit"]');
  saveBtn.disabled = true;
  const originalLabel = saveBtn.textContent;
  saveBtn.textContent = "Saving…";

  try {
    const res = currentEntryId
      ? await fetch(`api/entries/${currentEntryId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await fetch("api/log", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

    if (!res.ok) throw new Error(`server returned ${res.status}`);

    closeSheet();
    loadSummary();
    loadHistory();
  } catch (err) {
    alert("Couldn't save — check your connection and try again.");
  } finally {
    saveBtn.disabled = false;
    saveBtn.textContent = originalLabel;
  }
});

historyList.addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".delete-btn");
  if (deleteBtn) {
    e.stopPropagation();
    try {
      const res = await fetch(`api/entries/${deleteBtn.dataset.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`server returned ${res.status}`);
    } catch (err) {
      alert("Couldn't delete — check your connection and try again.");
    }
    loadSummary();
    loadHistory();
    return;
  }

  const item = e.target.closest(".history-item");
  if (item) {
    const entry = entriesCache[item.dataset.id];
    if (entry) openSheet(entry.type, entry);
  }
});

historyFilter.addEventListener("change", loadHistory);

document.getElementById("finance-prev-month").addEventListener("click", () => {
  financeMonth -= 1;
  if (financeMonth < 1) {
    financeMonth = 12;
    financeYear -= 1;
  }
  loadSummary();
});

document.getElementById("finance-next-month").addEventListener("click", () => {
  const isCurrentMonth =
    financeYear === CURRENT_DATE.getFullYear() && financeMonth === CURRENT_DATE.getMonth() + 1;
  if (isCurrentMonth) return;

  financeMonth += 1;
  if (financeMonth > 12) {
    financeMonth = 1;
    financeYear += 1;
  }
  loadSummary();
});

const backupBackdrop = document.getElementById("backup-backdrop");
const backupOpenBtn = document.getElementById("backup-open-btn");
const backupCloseBtn = document.getElementById("backup-close-btn");
const restoreBtn = document.getElementById("restore-btn");
const restoreFile = document.getElementById("restore-file");

backupOpenBtn.addEventListener("click", () => backupBackdrop.classList.add("open"));
backupCloseBtn.addEventListener("click", () => backupBackdrop.classList.remove("open"));
backupBackdrop.addEventListener("click", (e) => {
  if (e.target === backupBackdrop) backupBackdrop.classList.remove("open");
});

restoreBtn.addEventListener("click", async () => {
  const file = restoreFile.files[0];
  if (!file) {
    alert("Choose a backup file first.");
    return;
  }
  if (!confirm("This will replace all current entries with the contents of this backup. Continue?")) {
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("api/restore", { method: "POST", body: formData });
    if (res.ok) {
      alert("Backup restored.");
      restoreFile.value = "";
      backupBackdrop.classList.remove("open");
      loadSummary();
      loadHistory();
    } else {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "Restore failed.");
    }
  } catch (err) {
    alert("Couldn't reach the server — check your connection and try again.");
  }
});

const notifyBackdrop = document.getElementById("notify-backdrop");
const notifyOpenBtn = document.getElementById("notify-open-btn");
const notifyCloseBtn = document.getElementById("notify-close-btn");
const notifyTestBtn = document.getElementById("notify-test-btn");
const notifyTestResult = document.getElementById("notify-test-result");
const debugToggle = document.getElementById("debug-toggle");
const debugList = document.getElementById("debug-list");

const DEBUG_LABELS = {
  app_version: "App version",
  container_time: "Container time",
  container_timezone: "Container timezone",
  supervisor_token_set: "SUPERVISOR_TOKEN set",
  ha_api_reachable: "HA API reachable",
  ha_api_error: "HA API error",
  ha_location_name: "HA location",
  ha_time_zone: "HA timezone",
  options_path: "Options file",
  options_path_exists: "Options file exists",
  db_path: "Database path",
  db_ok: "Database OK",
  db_error: "Database error",
  reminder_last_checked_date: "Reminder last checked",
  python_version: "Python version",
  flask_version: "Flask version",
  platform: "Platform",
};

function debugValueHtml(key, value) {
  if (value === null || value === undefined || value === "") return "<em>–</em>";
  if (typeof value === "boolean") {
    const label = value ? "yes" : "no";
    const cls = key.endsWith("_error") ? "" : value ? "debug-ok" : "debug-fail";
    return `<span class="${cls}">${label}</span>`;
  }
  return escapeHtml(String(value));
}

async function loadDebugInfo() {
  debugList.innerHTML = "<dt>Loading…</dt>";
  try {
    const res = await fetch("api/debug");
    const data = await res.json();
    debugList.innerHTML = Object.entries(DEBUG_LABELS)
      .map(([key, label]) => `<dt>${label}</dt><dd>${debugValueHtml(key, data[key])}</dd>`)
      .join("");
  } catch (e) {
    debugList.innerHTML = "<dt>Error</dt><dd>Could not reach the server.</dd>";
  }
}

debugToggle.addEventListener("click", () => {
  const isHidden = debugList.hidden;
  debugList.hidden = !isHidden;
  debugToggle.textContent = isHidden ? "Debug info ▴" : "Debug info ▾";
  if (isHidden) loadDebugInfo();
});

async function loadNotifyPanel() {
  const list = document.getElementById("notify-services-list");
  list.innerHTML = '<li class="notify-services-empty">Loading…</li>';
  try {
    const res = await fetch("api/notifications");
    const data = await res.json();
    document.getElementById("notify-enabled").textContent = data.reminder.enabled ? "On" : "Off";
    document.getElementById("notify-time").textContent = data.reminder.check_time;
    document.getElementById("notify-threshold").textContent = `${data.reminder.threshold_days} days`;
    document.getElementById("notify-service").textContent = data.reminder.notify_service || "Not set";

    if (data.services_error) {
      list.innerHTML = `<li class="notify-services-empty">${escapeHtml(data.services_error)}</li>`;
    } else if (!data.services.length) {
      list.innerHTML =
        '<li class="notify-services-empty">No notify services found. Make sure the Home Assistant Companion App is installed on your phone.</li>';
    } else {
      list.innerHTML = data.services.map((s) => `<li>notify.${escapeHtml(s)}</li>`).join("");
    }
  } catch (e) {
    list.innerHTML = '<li class="notify-services-empty">Could not reach the server.</li>';
  }
}

notifyOpenBtn.addEventListener("click", () => {
  notifyBackdrop.classList.add("open");
  notifyTestResult.textContent = "";
  debugList.hidden = true;
  debugToggle.textContent = "Debug info ▾";
  loadNotifyPanel();
});
notifyCloseBtn.addEventListener("click", () => notifyBackdrop.classList.remove("open"));
notifyBackdrop.addEventListener("click", (e) => {
  if (e.target === notifyBackdrop) notifyBackdrop.classList.remove("open");
});

notifyTestBtn.addEventListener("click", async () => {
  notifyTestBtn.disabled = true;
  notifyTestResult.textContent = "Sending…";
  try {
    const res = await fetch("api/notify-test", { method: "POST" });
    const data = await res.json();
    notifyTestResult.textContent =
      data.status === "sent" ? "Test notification sent!" : `Failed: ${data.error || "unknown error"}`;
  } catch (e) {
    notifyTestResult.textContent = "Failed to reach server.";
  } finally {
    notifyTestBtn.disabled = false;
  }
});

const tabButtons = document.querySelectorAll(".tabbar-btn");
const trendsRangeSelect = document.getElementById("trends-range");
const trendsChartWrap = document.getElementById("trends-chart-wrap");
const trendsEmpty = document.getElementById("trends-empty");
const trendsTableBody = document.getElementById("trends-table-body");
const trendsForecastCaption = document.getElementById("trends-forecast-caption");
const trendsExpandBtn = document.getElementById("trends-expand-btn");

function setTrendsFullscreen(isFullscreen) {
  trendsChartWrap.classList.toggle("is-fullscreen", isFullscreen);
  trendsExpandBtn.textContent = isFullscreen ? "✕" : "⛶";
  trendsExpandBtn.setAttribute("aria-label", isFullscreen ? "Collapse chart" : "Expand chart");
}

trendsExpandBtn.addEventListener("click", () => {
  setTrendsFullscreen(!trendsChartWrap.classList.contains("is-fullscreen"));
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && trendsChartWrap.classList.contains("is-fullscreen")) {
    setTrendsFullscreen(false);
  }
});

function monthLabel(ym) {
  const [year, month] = ym.split("-").map(Number);
  return `${MONTH_NAMES[month - 1].slice(0, 3)} ${year}`;
}

function buildTrendsSvg(data) {
  const pointSpacing = 48;
  const chartH = 120;
  const topPad = 10;
  const labelH = 16;
  const forecastMonths = data.forecast_months || [];
  const forecastCollected = data.forecast_collected || [];
  const forecastBacktest = data.forecast_backtest || [];
  const historyCount = data.months.length;
  const totalCount = historyCount + forecastMonths.length;
  const width = totalCount * pointSpacing;
  const height = topPad + chartH + labelH;
  const maxVal = Math.max(
    1,
    ...data.collected,
    ...data.sold,
    ...data.used,
    ...forecastCollected,
    ...forecastBacktest
  );

  const xAt = (i) => i * pointSpacing + pointSpacing / 2;
  const yAt = (value) => topPad + chartH - (value / maxVal) * chartH;

  const line = (values, colorVar, { dashed = false, opacity = 1 } = {}) => {
    const points = values.map((v, i) => `${xAt(i)},${yAt(v)}`).join(" ");
    const dash = dashed ? ' stroke-dasharray="4,3"' : "";
    let svg = `<polyline points="${points}" fill="none" stroke="var(${colorVar})" stroke-width="2" stroke-opacity="${opacity}"${dash}></polyline>`;
    values.forEach((v, i) => {
      svg += `<circle cx="${xAt(i)}" cy="${yAt(v)}" r="2.5" fill="var(${colorVar})" fill-opacity="${opacity}"></circle>`;
    });
    return svg;
  };

  let content = "";
  content += line(data.sold, "--accent-sale");
  content += line(data.used, "--accent-used");
  // one continuous dashed line: backtest over history, projection over the future
  content += line([...forecastBacktest, ...forecastCollected], "--accent-egg", {
    dashed: true,
    opacity: 0.55,
  });
  content += line(data.collected, "--accent-egg");

  data.months.forEach((ym, i) => {
    content += `<text class="trends-bar-label" x="${xAt(i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });
  forecastMonths.forEach((ym, i) => {
    content += `<text class="trends-bar-label trends-bar-label-forecast" x="${xAt(historyCount + i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });

  let divider = "";
  if (forecastMonths.length > 0) {
    const dividerX = historyCount * pointSpacing;
    divider = `<line x1="${dividerX}" y1="${topPad}" x2="${dividerX}" y2="${topPad + chartH}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3"></line>`;
  }

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}${divider}</svg>`;
}

async function loadTrends() {
  const months = trendsRangeSelect.value;
  const res = await fetch(`api/trends?months=${months}`);
  const data = await res.json();

  const historyTotal = [...data.collected, ...data.sold, ...data.used].reduce((a, b) => a + b, 0);
  const forecastTotal = (data.forecast_collected || []).reduce((a, b) => a + b, 0);
  trendsEmpty.hidden = historyTotal > 0 || forecastTotal > 0;
  trendsChartWrap.querySelector("svg")?.remove();
  if (historyTotal > 0 || forecastTotal > 0) {
    trendsChartWrap.insertAdjacentHTML("beforeend", buildTrendsSvg(data));
  }

  const backtest = data.forecast_backtest || [];
  const historyRows = data.months
    .map(
      (ym, i) => `
        <tr>
          <td>${monthLabel(ym)}</td>
          <td>${data.collected[i]}</td>
          <td>${backtest[i]}</td>
          <td>${data.sold[i]}</td>
          <td>${data.used[i]}</td>
        </tr>
      `
    )
    .join("");

  const forecastRows = (data.forecast_months || [])
    .map(
      (ym, i) => `
        <tr class="trends-row-forecast">
          <td>${monthLabel(ym)} (forecast)</td>
          <td>–</td>
          <td>${data.forecast_collected[i]}</td>
          <td>–</td>
          <td>–</td>
        </tr>
      `
    )
    .join("");

  trendsTableBody.innerHTML = historyRows + forecastRows;

  trendsForecastCaption.textContent =
    data.forecast_basis === "breed_standard"
      ? "The dashed line is based on breed averages for your flock — log a few weeks of collection to refine it. It also shows what it would have predicted for past months, so you can see how it's tracking."
      : "The dashed line is based on breed averages for your flock, adjusted by your last 30 days of collection. Past months show what it would have predicted at the time, so you can see how it's tracking.";
}

function switchTab(pageId) {
  document.querySelectorAll(".page").forEach((page) => {
    page.hidden = page.id !== pageId;
  });
  tabButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.page === pageId));
  if (pageId !== "page-trends") setTrendsFullscreen(false);
  if (pageId === "page-trends") loadTrends();
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.page));
});

trendsRangeSelect.addEventListener("change", loadTrends);

loadSummary();
loadHistory();
