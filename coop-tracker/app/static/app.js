const ICONS = { egg: "🥚", cleaning: "🧹", feeding: "🌾" };
const TITLES = { egg: "Log Eggs", cleaning: "Log Cleaning", feeding: "Log Feeding" };

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

async function loadSummary() {
  const res = await fetch("api/summary");
  const data = await res.json();
  document.getElementById("stat-eggs-today").textContent = data.eggs_today;
  document.getElementById("stat-eggs-week").textContent = data.eggs_week;
  document.getElementById("stat-last-cleaning").textContent = fmtTime(data.last_cleaning);
  document.getElementById("stat-last-feeding").textContent = fmtTime(data.last_feeding);
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
    else title = `Fed${entry.food_type ? " " + entry.food_type : ""}${entry.amount ? " · " + entry.amount : ""}`;

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
        <input type="text" name="food_type" placeholder="e.g. layer feed, scratch grains" value="${entry ? entry.food_type ?? "" : ""}">
      </div>
      <div class="field">
        <label>Amount (optional)</label>
        <input type="text" name="amount" placeholder="e.g. 2 cups" value="${entry ? entry.amount ?? "" : ""}">
      </div>
      ${dateFieldHtml(tsValue)}
      <div class="field">
        <label>Notes (optional)</label>
        <textarea name="notes">${entry ? entry.notes ?? "" : ""}</textarea>
      </div>
    `;
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
  }

  if (currentEntryId) {
    await fetch(`api/entries/${currentEntryId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } else {
    await fetch("api/log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  closeSheet();
  loadSummary();
  loadHistory();
});

historyList.addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".delete-btn");
  if (deleteBtn) {
    e.stopPropagation();
    await fetch(`api/entries/${deleteBtn.dataset.id}`, { method: "DELETE" });
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

loadSummary();
loadHistory();
