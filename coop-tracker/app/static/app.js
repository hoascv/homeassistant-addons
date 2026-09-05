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
  loadFerment();
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

  document.getElementById("stat-savings-month").textContent = fmtMoney(data.savings_month);
  document.getElementById("stat-savings-total").textContent = fmtMoney(data.savings_total);

  // Coloured on its own sign, not on the plain net's: the whole point of the
  // tile is that a flock in the red on sales alone can still be ahead once the
  // eggs you ate are counted, and it has to be able to say so.
  for (const [id, value] of [["stat-net-savings-month", data.net_with_savings_month],
                             ["stat-net-savings-total", data.net_with_savings_total]]) {
    const el = document.getElementById(id);
    el.textContent = fmtMoney(value);
    el.classList.toggle("stat-positive", value >= 0);
    el.classList.toggle("stat-negative", value < 0);
  }

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
    else title = `${entry.count ?? 1} egg${entry.count === 1 ? "" : "s"} used${entry.given_away ? " · given away" : ""}`;

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

async function loadFoodTypeOptions(selectEl, currentValue = null) {
  try {
    const res = await fetch("api/food-types");
    const foodTypes = await res.json();
    selectEl.innerHTML = foodTypes
      .map((ft) => `<option value="${escapeHtml(ft.name)}">${escapeHtml(ft.name)}</option>`)
      .join("");
  } catch (err) {
    selectEl.innerHTML = "";
  }
  if (currentValue) ensureFoodTypeOption(selectEl, currentValue);
}

function ensureFoodTypeOption(selectEl, value) {
  if (!value) return;
  const hasOption = Array.from(selectEl.options).some((opt) => opt.value === value);
  if (!hasOption) {
    // Preserves a food type that was logged before it existed in the list
    // (or was since removed from it) instead of silently swapping it for
    // whatever the first option happens to be.
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = value;
    selectEl.insertBefore(opt, selectEl.firstChild);
  }
  selectEl.value = value;
}

async function prefillLastFoodType(selectEl) {
  try {
    const res = await fetch("api/entries?type=feeding&limit=1");
    const entries = await res.json();
    if (entries.length && entries[0].food_type) {
      ensureFoodTypeOption(selectEl, entries[0].food_type);
    }
  } catch (err) {
    // leave the default selection
  }
  updateFeedingStatsHint(selectEl.value);
}

async function renderFoodTypeManagerList() {
  const listEl = document.getElementById("food-type-manager-list");
  if (!listEl) return;
  listEl.innerHTML = "<li>Loading…</li>";
  try {
    const res = await fetch("api/food-types");
    const foodTypes = await res.json();
    listEl.innerHTML = foodTypes
      .map(
        (ft) => `
          <li>
            <span>${escapeHtml(ft.name)}</span>
            <button type="button" class="food-type-delete-btn" data-id="${ft.id}" aria-label="Remove ${escapeHtml(ft.name)}">✕</button>
          </li>
        `
      )
      .join("");
  } catch (err) {
    listEl.innerHTML = "<li>Could not load the list.</li>";
  }
}

function openSheet(type, entry = null) {
  currentType = type;
  currentEntryId = entry ? entry.id : null;
  sheetTitle.textContent = entry ? `Edit ${TITLES[type].replace("Log ", "")}` : TITLES[type];
  sheetFields.innerHTML = "";
  delete sheetForm.dataset.eggSizes;
  pendingEggVisionSample = null;

  const tsValue = toLocalInputValue(entry ? new Date(entry.ts) : new Date());

  if (type === "egg") {
    const initialCount = entry ? entry.count ?? 1 : 1;
    if (entry && entry.egg_sizes) sheetForm.dataset.eggSizes = entry.egg_sizes;
    sheetFields.innerHTML = `
      <div class="field">
        <label>Eggs collected</label>
        <div class="stepper">
          <button type="button" id="dec">−</button>
          <span id="count-value">${initialCount}</span>
          <button type="button" id="inc">+</button>
        </div>
      </div>
      ${
        window.EGG_VISION && window.EGG_VISION.enabled
          ? `<div class="field">
               <button type="button" class="link-btn" id="egg-photo-btn">📷 Count &amp; size from a photo</button>
               <input type="file" id="egg-photo-input" accept="image/*" capture="environment" hidden>
             </div>`
          : ""
      }
      ${
        entry && entry.egg_sizes
          ? `<div class="field"><label>Sizes</label><p class="egg-sizes-readout" id="egg-sizes-readout">${escapeHtml(entry.egg_sizes)}</p></div>`
          : ""
      }
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
    const photoBtn = document.getElementById("egg-photo-btn");
    if (photoBtn) {
      photoBtn.addEventListener("click", () => {
        if (!window.EGG_VISION.available) {
          alert("Not available on this device's architecture (requires amd64 or aarch64).");
          return;
        }
        document.getElementById("egg-photo-input").click();
      });
      document.getElementById("egg-photo-input").addEventListener("change", async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        await startEggVisionReview(file);
        e.target.value = "";
      });
    }
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
        <div class="field-label-row">
          <label>Food type</label>
          <button type="button" class="link-btn" id="food-type-manage-btn">Manage list</button>
        </div>
        <select name="food_type" id="feeding-food-type">
          <option>Loading…</option>
        </select>
      </div>
      <div class="food-type-manager" id="food-type-manager" hidden>
        <ul class="food-type-manager-list" id="food-type-manager-list"></ul>
        <div class="food-type-manager-add">
          <input type="text" id="food-type-new-input" placeholder="Add a new food type">
          <button type="button" class="btn-secondary" id="food-type-add-btn">Add</button>
        </div>
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

    const foodTypeSelect = document.getElementById("feeding-food-type");
    const foodTypeManageBtn = document.getElementById("food-type-manage-btn");
    const foodTypeManager = document.getElementById("food-type-manager");
    const foodTypeNewInput = document.getElementById("food-type-new-input");
    const foodTypeAddBtn = document.getElementById("food-type-add-btn");

    foodTypeSelect.addEventListener("change", () => updateFeedingStatsHint(foodTypeSelect.value));

    foodTypeManageBtn.addEventListener("click", () => {
      const isHidden = foodTypeManager.hidden;
      foodTypeManager.hidden = !isHidden;
      if (isHidden) renderFoodTypeManagerList();
    });

    foodTypeManager.addEventListener("click", async (e) => {
      const deleteBtn = e.target.closest(".food-type-delete-btn");
      if (!deleteBtn) return;
      await fetch(`api/food-types/${deleteBtn.dataset.id}`, { method: "DELETE" });
      await loadFoodTypeOptions(foodTypeSelect, foodTypeSelect.value);
      renderFoodTypeManagerList();
    });

    foodTypeAddBtn.addEventListener("click", async () => {
      const name = foodTypeNewInput.value.trim();
      if (!name) return;
      const previousValue = foodTypeSelect.value;
      const res = await fetch("api/food-types", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (res.ok) {
        foodTypeNewInput.value = "";
        await loadFoodTypeOptions(foodTypeSelect, previousValue); // keep the current selection, don't jump to the new one
        renderFoodTypeManagerList();
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || "Couldn't add that food type.");
      }
    });

    if (entry) {
      loadFoodTypeOptions(foodTypeSelect, entry.food_type).then(() =>
        updateFeedingStatsHint(foodTypeSelect.value)
      );
    } else {
      loadFoodTypeOptions(foodTypeSelect).then(() => prefillLastFoodType(foodTypeSelect));
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
      <!-- Above the fields, because it fills them in. Hidden where the OCR
           engine is not installed rather than offered and then apologised for. -->
      <div class="field receipt-scan" id="receipt-scan" hidden>
        <button type="button" class="btn-secondary full" id="receipt-btn">
          📷 Scan a receipt</button>
        <input type="file" id="receipt-file" accept="image/*" capture="environment" hidden>
        <p class="receipt-note" id="receipt-note" hidden></p>
        <div class="receipt-choices" id="receipt-choices" hidden></div>
      </div>
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
    // Asked once and remembered: the answer cannot change while the page is
    // open, and the sheet should not wait on a request to draw itself.
    revealReceiptScan();
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
      <label class="field-checkbox">
        <input type="checkbox" name="given_away" id="used-given-away" ${entry && entry.given_away ? "checked" : ""}>
        Given away
      </label>
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
  pendingEggVisionSample = null;
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
    payload.egg_sizes = sheetForm.dataset.eggSizes || null;
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
    payload.given_away = document.getElementById("used-given-away").checked;
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

    if (currentType === "egg" && pendingEggVisionSample) {
      // Fire-and-forget — a failed sample upload shouldn't affect the
      // save itself, this is purely observability for later training.
      fetch("api/vision/eggs/sample", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(pendingEggVisionSample),
      }).catch(() => {});
      pendingEggVisionSample = null;
    }

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

// --- Egg photo count & size review (Log Eggs sheet) ---
//
// A photo is analyzed server-side (POST /api/vision/eggs) against a
// registered nesting box's known inside width — the box's own side
// walls are the in-frame scale reference, not a coin, since the camera
// is handheld (see ARCHITECTURE.md §20 addendum). Wall position can
// always be dragged into place here, since auto-detection is a best
// guess, never authoritative — a missed/wrong wall must never block
// logging. If training is enabled, the corrected result (and the photo)
// get stored server-side after a successful save, to fit a trainable
// model against this install's own corrections.

const EGG_SIZE_COLORS = { S: "#8aa9c9", M: "#6fb37a", L: "#d9a441", XL: "#c05d5d" };
const EGG_SIZE_CYCLE = ["S", "M", "L", "XL"];
// Keep in sync with app.py's EGG_VISION_WIZARD_STREAK_TARGET/MAX_ATTEMPTS
// — purely a client-side UX pacing choice, the server doesn't enforce
// these itself.
const EGG_VISION_WIZARD_STREAK_TARGET = 3;
const EGG_VISION_WIZARD_MAX_ATTEMPTS = 30;

const eggVisionBackdrop = document.getElementById("egg-vision-backdrop");
const eggVisionNoBoxPanel = document.getElementById("egg-vision-no-box");
const eggVisionConfirmBoxPanel = document.getElementById("egg-vision-confirm-box");
const eggVisionReviewPanel = document.getElementById("egg-vision-review");
const eggVisionCanvasWrap = document.getElementById("egg-vision-canvas-wrap");
const eggVisionPhotoImg = document.getElementById("egg-vision-photo");
const eggVisionOverlay = document.getElementById("egg-vision-overlay");
const eggVisionStatusMsg = document.getElementById("egg-vision-status-msg");
const eggVisionChips = document.getElementById("egg-vision-chips");
const eggVisionUseBtn = document.getElementById("egg-vision-use-btn");
const eggVisionCancelBtn = document.getElementById("egg-vision-cancel-btn");
const eggVisionWizardNextBtn = document.getElementById("egg-vision-wizard-next-btn");
const eggVisionWizardFinishBtn = document.getElementById("egg-vision-wizard-finish-btn");
const eggVisionWizardProgress = document.getElementById("egg-vision-wizard-progress");

// eggVisionState: { boxId, boxWidthMm, imageWidth, imageHeight,
//   boxWalls: {top_y,bottom_y,left_top_x,left_bottom_x,right_top_x,right_bottom_x},
//   eggs: [{cx,cy,widthPx,heightPx,angle,size,manuallySet,added}] }
let eggVisionState = null;
let eggVisionDrag = null; // {wall:"left"|"right"} | {kind:"egg", index}
let eggVisionOriginal = null; // frozen pre-correction detection, snake_case, for the training sample
let eggVisionPhotoDataUri = null; // cached exact bytes analysis was run against
let eggVisionTouched = false; // did the user change anything from the auto-detected result?
let eggVisionWizard = null; // null, or {boxId, boxName, streak, attempts}
let pendingEggVisionSample = null; // built on "Use these results", submitted after a successful save
let eggVisionEditSampleId = null; // set while re-correcting a stored training photo from the gallery

function eggToSnakeCase(e) {
  return {
    cx: e.cx,
    cy: e.cy,
    width_px: e.widthPx,
    height_px: e.heightPx,
    angle: e.angle,
    size: e.size,
    manually_set: e.manuallySet,
    added: e.added,
  };
}

function buildEggVisionSamplePayload(source) {
  const payload = {
    photo: eggVisionPhotoDataUri,
    original: eggVisionOriginal,
    corrected: {
      box_id: eggVisionState.boxId,
      box_width_mm: eggVisionState.boxWidthMm,
      box_walls: eggVisionState.boxWalls,
      eggs: eggVisionState.eggs.map(eggToSnakeCase),
    },
  };
  if (source) payload.source = source;
  return payload;
}

function eggVisionShowPanel(panel) {
  eggVisionNoBoxPanel.hidden = panel !== "no_box";
  eggVisionConfirmBoxPanel.hidden = panel !== "confirm_box";
  eggVisionReviewPanel.hidden = panel !== "review";
}

async function startEggVisionReview(file, opts = {}) {
  eggVisionBackdrop.classList.add("open");
  eggVisionShowPanel("review");
  eggVisionUseBtn.disabled = true;
  eggVisionCanvasWrap.hidden = true;
  eggVisionChips.innerHTML = "";
  eggVisionStatusMsg.textContent = "Analyzing…";

  let dataUri;
  try {
    dataUri = await resizeImageToDataUri(file, 1600, 0.85);
  } catch (err) {
    eggVisionStatusMsg.textContent = "Couldn't read that photo — try again.";
    return;
  }
  eggVisionPhotoDataUri = dataUri;
  await analyzeEggVisionPhoto(opts);
}

async function analyzeEggVisionPhoto(opts = {}) {
  eggVisionTouched = false;
  document.getElementById("egg-vision-title").textContent = eggVisionWizard
    ? `Teach it to spot "${eggVisionWizard.boxName}"`
    : "Count & size from a photo";
  eggVisionUseBtn.hidden = !!eggVisionWizard;
  eggVisionCancelBtn.hidden = !!eggVisionWizard;
  eggVisionWizardNextBtn.hidden = !eggVisionWizard;
  eggVisionWizardFinishBtn.hidden = !eggVisionWizard;
  eggVisionWizardProgress.hidden = !eggVisionWizard;
  eggVisionShowPanel("review");
  eggVisionUseBtn.disabled = true;
  eggVisionCanvasWrap.hidden = true;
  eggVisionChips.innerHTML = "";
  eggVisionStatusMsg.textContent = "Analyzing…";

  let body;
  try {
    const res = await fetch("api/vision/eggs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photo: eggVisionPhotoDataUri, box_id: opts.boxId ?? undefined }),
    });
    body = await res.json();
  } catch (err) {
    eggVisionStatusMsg.textContent = "Couldn't reach the server — check your connection.";
    return;
  }

  if (body.status === "no_boxes_registered") {
    eggVisionShowPanel("no_box");
    return;
  }
  if (body.status === "confirm_box") {
    const list = document.getElementById("egg-vision-box-candidates");
    list.innerHTML = body.box_candidates
      .map(
        (b) =>
          `<li><button type="button" class="link-btn egg-vision-box-candidate" data-box-id="${b.id}">${escapeHtml(b.name)}</button></li>`
      )
      .join("");
    eggVisionShowPanel("confirm_box");
    return;
  }
  if (body.status === "disabled" || body.status === "libs_unavailable" || body.status === "error") {
    eggVisionStatusMsg.textContent =
      body.status === "error"
        ? "Couldn't analyze that photo — try a different one, or log counts manually."
        : "Photo analysis isn't available right now.";
    return;
  }

  eggVisionState = {
    boxId: body.box.id,
    boxWidthMm: body.box.width_mm,
    imageWidth: body.image_width,
    imageHeight: body.image_height,
    boxWalls: { ...body.box_walls },
    eggs: body.eggs.map((e) => ({
      cx: e.cx,
      cy: e.cy,
      widthPx: e.width_px,
      heightPx: e.height_px,
      angle: e.angle,
      size: e.size || "M",
      manuallySet: false,
      added: false,
    })),
  };
  eggVisionOriginal = {
    box_id: body.box.id,
    box_width_mm: body.box.width_mm,
    box_walls: { ...body.box_walls },
    eggs: eggVisionState.eggs.map(eggToSnakeCase),
  };

  eggVisionPhotoImg.src = eggVisionPhotoDataUri;
  eggVisionPhotoImg.onload = () => {
    eggVisionCanvasWrap.hidden = false;
    drawEggVisionOverlay();
    renderEggVisionChips();
  };

  eggVisionStatusMsg.textContent =
    body.status === "walls_not_found"
      ? "Couldn't find the box's edges automatically — drag the line ends onto the box's side walls (tilt them to match an angled photo)."
      : body.status === "no_eggs_found"
      ? "Couldn't detect any eggs — you can still log a count manually below, or use + Add egg."
      : "Drag a line if it isn't on the box's edge. Tap a size chip to correct it.";
  eggVisionUseBtn.disabled = false;
  updateWizardProgress();
}

async function recomputeEggSizes() {
  // Box-width scaling is a plain division (no perspective correction —
  // see ARCHITECTURE.md §20 addendum), but sizing once a trained model
  // exists needs scikit-learn, so this still round-trips to the server
  // rather than being ported to JS.
  if (!eggVisionState) return;
  try {
    const res = await fetch("api/vision/eggs/recompute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        box_id: eggVisionState.boxId,
        box_walls: eggVisionState.boxWalls,
        eggs: eggVisionState.eggs.map((e) => ({
          cx: e.cx,
          cy: e.cy,
          width_px: e.widthPx,
          height_px: e.heightPx,
          angle: e.angle,
          aspect_ratio: e.heightPx / e.widthPx,
          extent: 1.0,
        })),
      }),
    });
    const body = await res.json();
    if (Array.isArray(body.eggs)) {
      body.eggs.forEach((r, i) => {
        const egg = eggVisionState.eggs[i];
        if (egg && !egg.manuallySet && r.size) egg.size = r.size;
      });
    }
  } catch (err) {
    // Best-effort — leave sizes as-is if the recompute round trip fails.
  }
  drawEggVisionOverlay();
  renderEggVisionChips();
}

function eggVisionDisplayScale() {
  return eggVisionPhotoImg.clientWidth / eggVisionState.imageWidth;
}

function drawEggVisionOverlay() {
  if (!eggVisionState) return;
  const scale = eggVisionDisplayScale();
  const canvas = eggVisionOverlay;
  canvas.width = eggVisionPhotoImg.clientWidth;
  canvas.height = eggVisionPhotoImg.clientHeight;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  eggVisionState.eggs.forEach((egg) => {
    ctx.save();
    ctx.translate(egg.cx * scale, egg.cy * scale);
    ctx.rotate((egg.angle * Math.PI) / 180);
    ctx.strokeStyle = EGG_SIZE_COLORS[egg.size] || "#888";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.ellipse(0, 0, (egg.widthPx / 2) * scale, (egg.heightPx / 2) * scale, 0, 0, 2 * Math.PI);
    ctx.stroke();
    ctx.restore();
  });

  // Each wall is a (possibly slanted) line with a draggable handle at
  // both ends, so the trapezoid can follow walls converging with depth
  // in an angled handheld photo — see wallEndpoints().
  const walls = eggVisionState.boxWalls;
  ctx.strokeStyle = "#2b6cb0";
  ctx.fillStyle = "#2b6cb0";
  ctx.lineWidth = 3;
  [
    [walls.left_top_x, walls.left_bottom_x],
    [walls.right_top_x, walls.right_bottom_x],
  ].forEach(([topX, bottomX]) => {
    ctx.beginPath();
    ctx.moveTo(topX * scale, walls.top_y * scale);
    ctx.lineTo(bottomX * scale, walls.bottom_y * scale);
    ctx.stroke();
    [[topX, walls.top_y], [bottomX, walls.bottom_y]].forEach(([hx, hy]) => {
      ctx.beginPath();
      ctx.arc(hx * scale, hy * scale, 6, 0, 2 * Math.PI);
      ctx.fill();
    });
  });
}

function wallEndpoints() {
  const walls = eggVisionState.boxWalls;
  return [
    { xKey: "left_top_x", yKey: "top_y", x: walls.left_top_x, y: walls.top_y },
    { xKey: "left_bottom_x", yKey: "bottom_y", x: walls.left_bottom_x, y: walls.bottom_y },
    { xKey: "right_top_x", yKey: "top_y", x: walls.right_top_x, y: walls.top_y },
    { xKey: "right_bottom_x", yKey: "bottom_y", x: walls.right_bottom_x, y: walls.bottom_y },
  ];
}

function renderEggVisionChips() {
  eggVisionChips.innerHTML = eggVisionState.eggs
    .map(
      (egg, i) => `
        <span class="egg-chip" data-idx="${i}" style="background:${EGG_SIZE_COLORS[egg.size]}">
          ${egg.size}
          <button type="button" class="egg-chip-remove" data-idx="${i}" aria-label="Remove egg">✕</button>
        </span>
      `
    )
    .join("");
}

eggVisionChips.addEventListener("click", (e) => {
  const removeBtn = e.target.closest(".egg-chip-remove");
  if (removeBtn) {
    eggVisionState.eggs.splice(Number(removeBtn.dataset.idx), 1);
    eggVisionTouched = true;
    drawEggVisionOverlay();
    renderEggVisionChips();
    return;
  }
  const chip = e.target.closest(".egg-chip");
  if (chip) {
    const egg = eggVisionState.eggs[Number(chip.dataset.idx)];
    egg.size = EGG_SIZE_CYCLE[(EGG_SIZE_CYCLE.indexOf(egg.size) + 1) % EGG_SIZE_CYCLE.length];
    egg.manuallySet = true;
    eggVisionTouched = true;
    drawEggVisionOverlay();
    renderEggVisionChips();
  }
});

document.getElementById("egg-vision-add-egg").addEventListener("click", () => {
  if (!eggVisionState) return;
  const walls = eggVisionState.boxWalls;
  const span =
    (walls.right_top_x + walls.right_bottom_x - walls.left_top_x - walls.left_bottom_x) / 2;
  eggVisionState.eggs.push({
    cx: eggVisionState.imageWidth / 2,
    cy: eggVisionState.imageHeight / 2,
    widthPx: span * 0.06,
    heightPx: span * 0.08,
    angle: 0,
    size: "M",
    manuallySet: true,
    added: true,
  });
  eggVisionTouched = true;
  drawEggVisionOverlay();
  renderEggVisionChips();
});

// Drag: near a wall-line endpoint moves that endpoint (its x freely,
// and its shared top/bottom y — both walls' top handles ride top_y, both
// bottom handles ride bottom_y, keeping the trapezoid a trapezoid); near
// an egg's center moves that egg (the only way to place a "+ Add egg"
// marker on the actual missed egg, since it starts at the photo's
// center). Wall drags redraw instantly (pure rendering) but only
// trigger the mm/size recompute round trip on release, not on every
// pointermove. pointermove/up on document (not just the canvas) so a
// fast drag that briefly leaves the canvas bounds doesn't get dropped.
eggVisionOverlay.addEventListener("pointerdown", (e) => {
  if (!eggVisionState) return;
  const rect = eggVisionOverlay.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const scale = eggVisionDisplayScale();

  const endpoint = wallEndpoints().find(
    (p) => Math.hypot(x - p.x * scale, y - p.y * scale) < 16
  );
  if (endpoint) {
    eggVisionDrag = { wallEndpoint: endpoint };
    return;
  }

  const eggIndex = eggVisionState.eggs.findIndex(
    (egg) => Math.hypot(x - egg.cx * scale, y - egg.cy * scale) < 20
  );
  if (eggIndex !== -1) {
    eggVisionDrag = { kind: "egg", index: eggIndex };
  }
});

document.addEventListener("pointermove", (e) => {
  if (!eggVisionDrag || !eggVisionState) return;
  const rect = eggVisionOverlay.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const scale = eggVisionDisplayScale();

  if (eggVisionDrag.wallEndpoint) {
    const { xKey, yKey } = eggVisionDrag.wallEndpoint;
    eggVisionState.boxWalls[xKey] = x / scale;
    eggVisionState.boxWalls[yKey] = y / scale;
    eggVisionTouched = true;
  } else if (eggVisionDrag.kind === "egg") {
    const egg = eggVisionState.eggs[eggVisionDrag.index];
    if (egg) {
      egg.cx = x / scale;
      egg.cy = y / scale;
      eggVisionTouched = true;
    }
  }
  drawEggVisionOverlay();
  renderEggVisionChips();
});

document.addEventListener("pointerup", () => {
  if (eggVisionDrag && eggVisionDrag.wallEndpoint) recomputeEggSizes();
  eggVisionDrag = null;
});

function closeEggVisionReview() {
  const wasEditing = eggVisionEditSampleId !== null;
  eggVisionBackdrop.classList.remove("open");
  eggVisionState = null;
  eggVisionOriginal = null;
  eggVisionDrag = null;
  eggVisionWizard = null;
  eggVisionEditSampleId = null;
  eggVisionUseBtn.textContent = "Use these results";
  if (wasEditing) {
    // Return to the gallery the edit was launched from, refreshed.
    trainingGalleryBackdrop.classList.add("open");
    loadTrainingGallery();
  }
}

eggVisionCancelBtn.addEventListener("click", closeEggVisionReview);
document.getElementById("egg-vision-no-box-cancel-btn").addEventListener("click", closeEggVisionReview);
document.getElementById("egg-vision-confirm-box-cancel-btn").addEventListener("click", closeEggVisionReview);
eggVisionBackdrop.addEventListener("click", (e) => {
  if (e.target === eggVisionBackdrop) closeEggVisionReview();
});

eggVisionUseBtn.addEventListener("click", async () => {
  if (!eggVisionState) return;
  if (eggVisionEditSampleId !== null) {
    // Editing a stored training photo — save the re-corrected labels
    // back onto the sample rather than filling the Log Eggs sheet.
    const id = eggVisionEditSampleId;
    eggVisionUseBtn.disabled = true;
    try {
      const res = await fetch(`api/vision/samples/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ corrected: buildEggVisionSamplePayload().corrected }),
      });
      if (!res.ok) throw new Error(`server returned ${res.status}`);
    } catch (err) {
      alert("Couldn't save changes — check your connection and try again.");
      eggVisionUseBtn.disabled = false;
      return;
    }
    markTrainingDataDirty(); // edited labels only apply on the next retrain
    closeEggVisionReview(); // returns to the gallery + refreshes it
    return;
  }
  document.getElementById("count-value").textContent = eggVisionState.eggs.length;
  sheetForm.dataset.eggSizes = eggVisionState.eggs.map((e) => e.size).join(",");
  if (window.EGG_VISION.trainingEnabled) {
    pendingEggVisionSample = buildEggVisionSamplePayload();
  }
  closeEggVisionReview();
});

document.getElementById("egg-vision-box-candidates").addEventListener("click", async (e) => {
  const btn = e.target.closest(".egg-vision-box-candidate");
  if (!btn) return;
  await analyzeEggVisionPhoto({ boxId: Number(btn.dataset.boxId) });
});

// --- Nesting-box setup wizard ---
//
// Registers a box's known inside width, then loops guided photo capture
// + correction (reusing the review UI above in "wizard mode") until the
// freshly-retrained model gets EGG_VISION_WIZARD_STREAK_TARGET photos in
// a row exactly right — proving it's reliably correct, not lucky once —
// or EGG_VISION_WIZARD_MAX_ATTEMPTS is reached. See ARCHITECTURE.md §20
// addendum.

const boxSetupBackdrop = document.getElementById("box-setup-backdrop");

function openBoxSetup() {
  document.getElementById("box-setup-name").value = "";
  document.getElementById("box-setup-width").value = "";
  document.getElementById("box-setup-error").textContent = "";
  boxSetupBackdrop.classList.add("open");
}

function closeBoxSetup() {
  boxSetupBackdrop.classList.remove("open");
}

document.getElementById("egg-vision-setup-box-btn").addEventListener("click", () => {
  eggVisionBackdrop.classList.remove("open");
  openBoxSetup();
});
document.getElementById("egg-vision-new-box-btn").addEventListener("click", () => {
  eggVisionBackdrop.classList.remove("open");
  openBoxSetup();
});
document.getElementById("box-setup-cancel-btn").addEventListener("click", closeBoxSetup);
boxSetupBackdrop.addEventListener("click", (e) => {
  if (e.target === boxSetupBackdrop) closeBoxSetup();
});

document.getElementById("box-setup-continue-btn").addEventListener("click", async () => {
  const name = document.getElementById("box-setup-name").value.trim();
  const widthCm = parseFloat(document.getElementById("box-setup-width").value);
  const errorEl = document.getElementById("box-setup-error");
  if (!name) {
    errorEl.textContent = "Enter a name for this box.";
    return;
  }
  if (!widthCm || widthCm <= 0) {
    errorEl.textContent = "Enter a valid width.";
    return;
  }
  try {
    const res = await fetch("api/nesting-boxes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, width_mm: widthCm * 10 }),
    });
    const body = await res.json();
    if (!res.ok) {
      errorEl.textContent = body.error || "Couldn't add that box.";
      return;
    }
    closeBoxSetup();
    startEggVisionWizard(body.id, body.name);
    if (typeof loadNestingBoxes === "function") loadNestingBoxes();
  } catch (err) {
    errorEl.textContent = "Couldn't reach the server — check your connection.";
  }
});

function eggVisionPickPhotoForWizard() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.capture = "environment";
  input.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    await startEggVisionReview(file, { boxId: eggVisionWizard.boxId });
  });
  input.click();
}

function startEggVisionWizard(boxId, boxName) {
  eggVisionWizard = { boxId, boxName, streak: 0, attempts: 0 };
  eggVisionBackdrop.classList.add("open");
  eggVisionShowPanel("review");
  eggVisionPickPhotoForWizard();
}

function updateWizardProgress() {
  if (!eggVisionWizard) return;
  const { streak, attempts, boxName } = eggVisionWizard;
  const currentAttemptNumber = attempts + 1; // this photo, if submitted now
  const ready = streak >= EGG_VISION_WIZARD_STREAK_TARGET;
  const metMinimum = currentAttemptNumber >= coopBoxIdMinSamples;
  // Below the minimum, Finish is disabled outright — otherwise a box can
  // end up with too few samples for the auto-identification classifier
  // to ever train once a second box exists (see ARCHITECTURE.md §20
  // addendum / EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX).
  eggVisionWizardFinishBtn.disabled = !metMinimum;
  eggVisionWizardProgress.textContent = ready
    ? `✓ "${boxName}" looks reliable (${streak} correct in a row) — Finish, or keep going.`
    : !metMinimum
    ? `Photo ${currentAttemptNumber}/${coopBoxIdMinSamples} — need at least ${coopBoxIdMinSamples} before finishing, so it has enough to tell this box apart from others.`
    : `${streak}/${EGG_VISION_WIZARD_STREAK_TARGET} correct in a row · attempt ${attempts + 1}/${EGG_VISION_WIZARD_MAX_ATTEMPTS}`;
}

async function submitWizardSample() {
  if (!eggVisionState) return;
  eggVisionWizard.attempts += 1;
  eggVisionWizard.streak = eggVisionTouched ? 0 : eggVisionWizard.streak + 1;
  try {
    await fetch("api/vision/eggs/sample", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildEggVisionSamplePayload("wizard")),
    });
    await fetch("api/vision/train", { method: "POST" });
  } catch (err) {
    // Best-effort — the wizard still progresses even if a single
    // submit/retrain round trip fails; the next photo just won't
    // benefit from this one yet.
  }
}

eggVisionWizardNextBtn.addEventListener("click", async () => {
  eggVisionWizardNextBtn.disabled = true;
  eggVisionWizardNextBtn.textContent = "Updating…";
  await submitWizardSample();
  eggVisionWizardNextBtn.disabled = false;
  eggVisionWizardNextBtn.textContent = "Take another photo";
  if (eggVisionWizard.attempts >= EGG_VISION_WIZARD_MAX_ATTEMPTS) {
    finishEggVisionWizard();
    return;
  }
  eggVisionPickPhotoForWizard();
});

eggVisionWizardFinishBtn.addEventListener("click", async () => {
  eggVisionWizardFinishBtn.disabled = true;
  await submitWizardSample();
  eggVisionWizardFinishBtn.disabled = false;
  finishEggVisionWizard();
});

function finishEggVisionWizard() {
  const boxName = eggVisionWizard ? eggVisionWizard.boxName : "";
  closeEggVisionReview();
  alert(
    `"${boxName}" is ready to use. Turn on egg-vision training in the ⚙️ settings if you also want ongoing day-to-day corrections captured.`
  );
}

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

backupOpenBtn.addEventListener("click", () => {
  backupBackdrop.classList.add("open");
  loadNestingBoxes();
  loadTrainingStatus();
  loadAccessInfo();
});

async function loadAccessInfo() {
  try {
    const d = await fetch("api/debug").then((r) => r.json());
    document.getElementById("access-restricted-value").textContent = d.access_restricted ? "On" : "Off";
    document.getElementById("access-user-id").textContent = d.ingress_user_id || "unavailable";
  } catch (e) {
    document.getElementById("access-restricted-value").textContent = "–";
    document.getElementById("access-user-id").textContent = "–";
  }
}
backupCloseBtn.addEventListener("click", () => backupBackdrop.classList.remove("open"));
backupBackdrop.addEventListener("click", (e) => {
  if (e.target === backupBackdrop) backupBackdrop.classList.remove("open");
});

async function loadNestingBoxes() {
  const list = document.getElementById("nesting-boxes-list");
  list.innerHTML = '<li class="notify-services-empty">Loading…</li>';
  try {
    const [boxesRes, statusRes] = await Promise.all([fetch("api/nesting-boxes"), fetch("api/vision/train/status")]);
    const boxes = await boxesRes.json();
    const trainStatus = await statusRes.json().catch(() => null);
    const samplesPerBox = (trainStatus && trainStatus.samples_per_box) || {};
    const minPerBox = coopBoxIdMinSamples;
    list.innerHTML = boxes.length
      ? boxes
          .map((b) => {
            const n = samplesPerBox[String(b.id)] || samplesPerBox[b.id] || 0;
            const auto = boxes.length > 1 ? ` · ${n}/${minPerBox} auto-ID samples` : "";
            return `
              <li>
                ${escapeHtml(b.name)} — ${(b.width_mm / 10).toFixed(1)}cm${auto}
                <button type="button" class="link-btn nesting-box-train" data-id="${b.id}" data-name="${escapeHtml(b.name)}">+ Train more</button>
                <button type="button" class="link-btn nesting-box-delete" data-id="${b.id}" aria-label="Delete ${escapeHtml(b.name)}">✕</button>
              </li>
            `;
          })
          .join("")
      : '<li class="notify-services-empty">No nesting boxes set up yet.</li>';
  } catch (e) {
    list.innerHTML = '<li class="notify-services-empty">Could not reach the server.</li>';
  }
}

// Keep in sync with app.py's EGG_VISION_BOX_ID_MIN_SAMPLES_PER_BOX — a
// box needs at least this many samples before it's eligible for the
// auto-identification classifier once >=2 boxes exist.
const coopBoxIdMinSamples = 3;

document.getElementById("nesting-boxes-list").addEventListener("click", async (e) => {
  const trainBtn = e.target.closest(".nesting-box-train");
  if (trainBtn) {
    backupBackdrop.classList.remove("open");
    startEggVisionWizard(Number(trainBtn.dataset.id), trainBtn.dataset.name);
    return;
  }
  const btn = e.target.closest(".nesting-box-delete");
  if (!btn) return;
  if (!confirm("Delete this nesting box? Already-collected training photos are kept.")) return;
  try {
    await fetch(`api/nesting-boxes/${btn.dataset.id}`, { method: "DELETE" });
    loadNestingBoxes();
  } catch (err) {
    alert("Couldn't reach the server — check your connection and try again.");
  }
});

document.getElementById("nesting-boxes-add-btn").addEventListener("click", () => {
  backupBackdrop.classList.remove("open");
  openBoxSetup();
});

async function loadTrainingStatus() {
  try {
    const res = await fetch("api/vision/train/status");
    const body = await res.json();
    document.getElementById("training-enabled-value").textContent = body.training_enabled ? "On" : "Off";
    document.getElementById("training-sample-count").textContent = `${body.sample_count} / ${body.retention_count}`;
    const trainBtn = document.getElementById("training-train-btn");
    trainBtn.disabled = body.sample_count < body.min_samples_required;
    trainBtn.textContent =
      body.sample_count < body.min_samples_required
        ? `Train now (need ${body.min_samples_required - body.sample_count} more)`
        : "Train now";
  } catch (e) {
    document.getElementById("training-enabled-value").textContent = "–";
    document.getElementById("training-sample-count").textContent = "–";
  }
}

document.getElementById("training-train-btn").addEventListener("click", async () => {
  const btn = document.getElementById("training-train-btn");
  const resultEl = document.getElementById("training-train-result");
  btn.disabled = true;
  resultEl.textContent = "Training…";
  try {
    const res = await fetch("api/vision/train", { method: "POST" });
    const body = await res.json();
    if (body.status === "trained") {
      resultEl.textContent =
        `Counting model: ${body.classifier_trained ? `trained on ${body.classifier_positive_count}/${body.classifier_negative_count} examples` : "not enough data yet"}. ` +
        `Sizing model: ${body.size_model_trained ? `trained on ${body.size_model_sample_count} examples` : "not enough data yet"}.` +
        (body.box_classifier_trained ? ` Box recognition: trained on ${body.box_classifier_sample_count} examples.` : "");
    } else if (body.status === "insufficient_samples") {
      resultEl.textContent = `Need at least ${body.min_required} samples (have ${body.sample_count}).`;
    } else {
      resultEl.textContent = body.error || "Training isn't available right now.";
    }
  } catch (err) {
    resultEl.textContent = "Couldn't reach the server — check your connection.";
  }
  loadTrainingStatus();
});

document.getElementById("training-clear-btn").addEventListener("click", async () => {
  const status = await fetch("api/vision/train/status").then((r) => r.json()).catch(() => null);
  const count = status ? status.sample_count : "all";
  if (!confirm(`This will permanently delete ${count} stored training photos. Continue?`)) return;
  try {
    await fetch("api/vision/train/clear", { method: "POST" });
    document.getElementById("training-train-result").textContent = "Training photos cleared.";
  } catch (err) {
    alert("Couldn't reach the server — check your connection and try again.");
  }
  loadTrainingStatus();
});

// --- Training-photo gallery (inspect / edit / exclude stored samples) ---

const trainingGalleryBackdrop = document.getElementById("training-gallery-backdrop");
const trainingGalleryList = document.getElementById("training-gallery-list");

async function loadTrainingGallery() {
  trainingGalleryList.innerHTML = '<p class="sheet-subtext">Loading…</p>';
  try {
    const samples = await fetch("api/vision/samples").then((r) => r.json());
    if (!samples.length) {
      trainingGalleryList.innerHTML = '<p class="sheet-subtext">No training photos stored yet.</p>';
      return;
    }
    trainingGalleryList.innerHTML = samples
      .map((s) => {
        const sizes = (s.sizes || []).filter(Boolean).join(", ");
        const label = `${s.egg_count} egg${s.egg_count === 1 ? "" : "s"}${sizes ? ` · ${escapeHtml(sizes)}` : ""}`;
        const box = s.box_name ? escapeHtml(s.box_name) : "no box";
        // Excluded samples stay visible but greyed, and swap their actions:
        // Include puts them back, Delete removes them for good (a step that
        // only appears once a photo is already out of training).
        const actions = s.excluded
          ? `<button type="button" class="link-btn training-photo-include" data-id="${s.id}">Include</button>
             <button type="button" class="link-btn training-photo-delete" data-id="${s.id}">Delete</button>`
          : `<button type="button" class="link-btn training-photo-edit" data-id="${s.id}">Edit</button>
             <button type="button" class="link-btn training-photo-exclude" data-id="${s.id}">Exclude</button>`;
        return `
          <figure class="training-photo${s.excluded ? " excluded" : ""}" data-id="${s.id}">
            <img src="api/vision/samples/${s.id}/photo" alt="Training photo" loading="lazy">
            ${s.excluded ? '<span class="training-photo-badge">Excluded</span>' : ""}
            <figcaption>${label}<br><span class="training-photo-meta">${box}</span></figcaption>
            <div class="training-photo-actions">${actions}</div>
          </figure>`;
      })
      .join("");
  } catch (err) {
    trainingGalleryList.innerHTML = '<p class="sheet-subtext">Could not reach the server.</p>';
  }
}

document.getElementById("training-gallery-btn").addEventListener("click", () => {
  backupBackdrop.classList.remove("open");
  trainingGalleryBackdrop.classList.add("open");
  document.getElementById("training-nudge").hidden = !trainingDataDirty;
  loadTrainingGallery();
});
document.getElementById("training-gallery-close-btn").addEventListener("click", () => {
  trainingGalleryBackdrop.classList.remove("open");
});
trainingGalleryBackdrop.addEventListener("click", (e) => {
  if (e.target === trainingGalleryBackdrop) trainingGalleryBackdrop.classList.remove("open");
});

// Edits, exclude/include toggles and deletes only change what the NEXT
// train run learns from — nothing is applied until the user retrains. This
// flag drives the nudge bar so that's obvious instead of silently pending.
let trainingDataDirty = false;

function markTrainingDataDirty() {
  trainingDataDirty = true;
  document.getElementById("training-nudge").hidden = false;
}

async function setSampleExcluded(id, excluded) {
  try {
    await fetch(`api/vision/samples/${id}/excluded`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ excluded }),
    });
    markTrainingDataDirty();
    loadTrainingGallery();
  } catch (err) {
    alert("Couldn't reach the server — check your connection and try again.");
  }
}

trainingGalleryList.addEventListener("click", async (e) => {
  const excludeBtn = e.target.closest(".training-photo-exclude");
  if (excludeBtn) {
    setSampleExcluded(excludeBtn.dataset.id, true);
    return;
  }
  const includeBtn = e.target.closest(".training-photo-include");
  if (includeBtn) {
    setSampleExcluded(includeBtn.dataset.id, false);
    return;
  }
  const deleteBtn = e.target.closest(".training-photo-delete");
  if (deleteBtn) {
    if (!confirm("Permanently delete this photo? This can't be undone.")) return;
    try {
      await fetch(`api/vision/samples/${deleteBtn.dataset.id}`, { method: "DELETE" });
      markTrainingDataDirty();
      loadTrainingGallery();
    } catch (err) {
      alert("Couldn't reach the server — check your connection and try again.");
    }
    return;
  }
  const editBtn = e.target.closest(".training-photo-edit");
  if (editBtn) editEggVisionSample(Number(editBtn.dataset.id));
});

document.getElementById("training-nudge-retrain-btn").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Retraining…";
  try {
    const res = await fetch("api/vision/train", { method: "POST" });
    const body = await res.json();
    if (body.status === "trained") {
      trainingDataDirty = false;
      document.getElementById("training-nudge").hidden = true;
      loadTrainingStatus();
    } else if (body.status === "insufficient_samples") {
      alert(`Need at least ${body.min_required} samples to train (have ${body.sample_count}).`);
    } else {
      alert(body.error || "Training isn't available right now.");
    }
  } catch (err) {
    alert("Couldn't reach the server — check your connection and try again.");
  }
  btn.disabled = false;
  btn.textContent = original;
});

async function editEggVisionSample(sampleId) {
  let sample;
  try {
    sample = await fetch(`api/vision/samples/${sampleId}`).then((r) => r.json());
  } catch (err) {
    alert("Couldn't load that photo — check your connection and try again.");
    return;
  }
  const corrected = sample.corrected || {};
  const walls = corrected.box_walls;
  if (!walls) {
    alert("This photo can't be edited (missing box calibration). You can Remove it instead.");
    return;
  }

  eggVisionEditSampleId = sampleId;
  eggVisionWizard = null;
  eggVisionState = {
    boxId: corrected.box_id,
    boxWidthMm: corrected.box_width_mm,
    imageWidth: sample.image_width,
    imageHeight: sample.image_height,
    boxWalls: { ...walls },
    eggs: (corrected.eggs || []).map((e) => ({
      cx: e.cx,
      cy: e.cy,
      widthPx: e.width_px,
      heightPx: e.height_px,
      angle: e.angle,
      size: e.size || "M",
      manuallySet: e.manually_set !== undefined ? e.manually_set : true,
      added: !!e.added,
    })),
  };
  eggVisionOriginal = corrected; // unchanged for an edit — the photo isn't re-analyzed
  eggVisionTouched = false;

  // Configure the review panel for edit mode (no wizard buttons; the
  // primary button saves back to the sample — see the use-btn handler).
  document.getElementById("egg-vision-title").textContent = "Edit training photo";
  eggVisionUseBtn.hidden = false;
  eggVisionUseBtn.disabled = false;
  eggVisionUseBtn.textContent = "Save changes";
  eggVisionCancelBtn.hidden = false;
  eggVisionWizardNextBtn.hidden = true;
  eggVisionWizardFinishBtn.hidden = true;
  eggVisionWizardProgress.hidden = true;
  eggVisionShowPanel("review");
  eggVisionStatusMsg.textContent = "Drag a line if it isn't on the box's edge. Tap a size chip to correct it.";
  eggVisionChips.innerHTML = "";
  eggVisionCanvasWrap.hidden = true;

  trainingGalleryBackdrop.classList.remove("open");
  eggVisionBackdrop.classList.add("open");

  eggVisionPhotoImg.onload = () => {
    eggVisionCanvasWrap.hidden = false;
    drawEggVisionOverlay();
    renderEggVisionChips();
  };
  eggVisionPhotoImg.src = `api/vision/samples/${sampleId}/photo`;
}

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

// Every chart on this tab expands, not just the first one. These are small on a
// phone and the daily one packs ninety points into a few hundred pixels, which
// is precisely the chart worth making bigger.
//
// One delegated handler over the class rather than a listener per id: a fifth
// chart then needs a button and no JavaScript at all.
function setChartFullscreen(wrap, isFullscreen) {
  wrap.classList.toggle("is-fullscreen", isFullscreen);
  const button = wrap.querySelector(".trends-expand-btn");
  if (!button) return;
  button.textContent = isFullscreen ? "✕" : "⛶";
  button.setAttribute("aria-label", isFullscreen ? "Collapse chart" : "Expand chart");
}

// Kept as a named wrapper because the tab switcher calls it to collapse the
// chart on the way out — leaving one expanded over another page is how you get
// a chart floating above the Home tab.
function setTrendsFullscreen(isFullscreen) {
  for (const wrap of document.querySelectorAll(".trends-chart-wrap")) {
    if (isFullscreen === false || wrap === trendsChartWrap) {
      setChartFullscreen(wrap, isFullscreen);
    }
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest(".trends-expand-btn");
  if (!button) return;
  const wrap = button.closest(".trends-chart-wrap");
  if (wrap) setChartFullscreen(wrap, !wrap.classList.contains("is-fullscreen"));
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const wrap of document.querySelectorAll(".trends-chart-wrap.is-fullscreen")) {
    setChartFullscreen(wrap, false);
  }
});

// --- y axis ------------------------------------------------------------------
//
// Every chart on this page plots a countable thing against a category axis, and
// until now none of them said what the numbers were. A line that rises is not
// information until you know whether it rose by two eggs or two hundred.
//
// Ported from Electricity Tracker's yAxisTicks rather than invented again, so
// the two add-ons pick gridlines the same way and a reader moving between them
// is not learning a second convention.

function axisTicks(max, unit, wanted = 3) {
  // 1, 2 or 5 times a power of ten: the intervals people read without doing
  // arithmetic. Anything else and a gridline needs working out. The thresholds
  // are the midpoints between those choices, so the step picked is the one
  // whose tick count comes closest to what was asked for.
  const rough = max / wanted;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rough)));
  const normalised = rough / magnitude;
  // Never finer than 0.1. Eggs are counted, and the finest thing quoted here is
  // an average like 3.4/day; a 0.05 step would label gridlines with numbers
  // they do not sit on.
  const step = Math.max(0.1,
    (normalised < 1.5 ? 1 : normalised < 3 ? 2 : normalised < 7 ? 5 : 10) * magnitude);
  const decimals = Math.max(0, Math.ceil(-Math.log10(step)));

  const ticks = [];
  const seen = new Set();
  const slack = step / 1000;
  for (let v = 0; v <= max + slack; v += step) {
    const label = v.toFixed(decimals);
    if (seen.has(label)) continue;
    seen.add(label);
    ticks.push({ value: v, label });
  }
  if (!ticks.length) ticks.push({ value: 0, label: "0" });
  // The unit rides on the topmost tick, so it is stated once without needing a
  // rotated axis title that costs more width than the labels themselves.
  ticks[ticks.length - 1].label += ` ${unit}`;
  return ticks;
}

// The gutter has to be measured before anything is placed horizontally, which
// is why this returns it rather than drawing straight away: 8px type runs about
// 4.6 user units to the character, and a gutter narrower than the widest label
// puts the axis on top of the data.
function chartYAxis(maxVal, unit, { topPad, chartH }) {
  const ticks = axisTicks(maxVal, unit);
  const widest = Math.max(...ticks.map((t) => t.label.length));
  const gutter = Math.round(widest * 4.6) + 8;
  const yAt = (value) => topPad + chartH - (value / maxVal) * chartH;

  return {
    gutter,
    // Gridlines belong behind the data, so callers prepend this.
    render: (width) => ticks.map(({ value, label }) => {
      const y = yAt(value).toFixed(2);
      return `<line class="chart-grid-line" x1="${gutter}" y1="${y}" x2="${width}" y2="${y}"></line>`
        + `<text class="chart-axis-value" x="${gutter - 5}" y="${y}" text-anchor="end"`
        + ` dominant-baseline="middle">${escapeHtml(label)}</text>`;
    }).join(""),
  };
}

// Hover targets. An invisible circle per point with an SVG <title>, which is
// what the browser turns into a tooltip — the same mechanism Electricity
// Tracker uses, so the two add-ons behave alike. Without them these charts had
// no way at all to read an exact date or value off a line.
function hitTargets(points, labelOf, dayOf) {
  return points.map((p, i) => {
    if (!p) return "";
    const text = labelOf(i);
    if (!text) return "";
    // Only points that can answer a drill-down carry a day, and the cursor
    // follows: a month's figure averages thirty collections and there is no
    // single set of entries behind it to show.
    const day = dayOf ? dayOf(i) : null;
    // data-tip, not <title>: the browser draws its own tooltip from a <title>
    // after about a second, so keeping both means two tooltips, one of them
    // late and ugly. aria-label keeps the value available to a screen reader.
    return `<circle class="chart-hit${day ? " is-drillable" : ""}"`
      + ` cx="${p.x.toFixed(2)}" cy="${p.y.toFixed(2)}" r="8"`
      + (day ? ` data-day="${escapeHtml(day)}"` : "")
      + ` data-tip="${escapeHtml(text)}" aria-label="${escapeHtml(text)}"></circle>`;
  }).join("");
}

// A hen lays at most one egg a day, so the flock size is a hard ceiling on any
// eggs-per-day figure. Drawn because it turns the chart from "some numbers" into
// "how close are they to their best", and because it makes a rate above it —
// which is a data problem, not a bumper harvest — visible instead of plausible.
function flockCeiling(birds, yAt, maxVal, gutter, width) {
  if (!birds || birds <= 0 || birds > maxVal) return "";
  const y = yAt(birds).toFixed(2);
  // Labelled at the left end, not the right: the expand button sits in the
  // top-right corner of every one of these charts, and a right-aligned label
  // near the top of the plot lands underneath it.
  return `<line class="chart-ceiling" x1="${gutter}" y1="${y}" x2="${width}" y2="${y}"></line>`
    + `<text class="chart-ceiling-label" x="${gutter + 3}" y="${(Number(y) - 2.5).toFixed(2)}">`
    + `${birds} hens</text>`;
}

function monthLabel(ym) {
  const [year, month] = ym.split("-").map(Number);
  return `${MONTH_NAMES[month - 1].slice(0, 3)} ${year}`;
}

// xs/ysUpper/ysLower are already-transformed pixel coordinates (not raw
// data values) — keeps this a pure SVG-string builder, reusable by any
// chart regardless of that chart's own value-to-pixel scale.
function bandPolygon(xs, ysUpper, ysLower, colorVar) {
  const top = xs.map((x, i) => `${x},${ysUpper[i]}`);
  const bottom = xs.map((x, i) => `${x},${ysLower[i]}`).reverse();
  return `<polygon points="${[...top, ...bottom].join(" ")}" fill="var(${colorVar})" fill-opacity="0.12" stroke="none"></polygon>`;
}

function buildTrendsSvg(data) {
  const pointSpacing = 48;
  const chartH = 120;
  const topPad = 10;
  const labelH = 16;
  const forecastMonths = data.forecast_months || [];
  const forecastCollected = data.forecast_collected || [];
  const forecastBacktest = data.forecast_backtest || [];
  const margin = data.forecast_margin;
  const historyCount = data.months.length;
  const totalCount = historyCount + forecastMonths.length;
  const plotW = totalCount * pointSpacing;
  const height = topPad + chartH + labelH;
  const maxVal = Math.max(
    1,
    ...data.collected,
    ...data.sold,
    ...data.used,
    ...forecastCollected,
    ...forecastBacktest,
    ...(margin != null ? forecastCollected.map((v) => v + margin) : [])
  );

  const axis = chartYAxis(maxVal, "eggs", { topPad, chartH });
  // The gutter is added beside the plot, never taken out of it: an axis that
  // narrowed the chart would redraw the data every time a label got wider.
  const width = axis.gutter + plotW;
  const xAt = (i) => axis.gutter + i * pointSpacing + pointSpacing / 2;
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

  let content = axis.render(width);
  if (margin != null && forecastCollected.length > 0) {
    const xs = forecastCollected.map((_, i) => xAt(historyCount + i));
    const ysUpper = forecastCollected.map((v) => yAt(v + margin));
    const ysLower = forecastCollected.map((v) => yAt(Math.max(0, v - margin)));
    content += bandPolygon(xs, ysUpper, ysLower, "--accent-egg");
  }
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

  // One target per month covering all three series, since the tooltip names
  // them all — three overlapping circles would just fight each other.
  content += hitTargets(
    data.months.map((_, i) => ({ x: xAt(i), y: yAt(data.collected[i]), i })),
    (i) => `${monthLabel(data.months[i])} — ${data.collected[i]} collected, `
           + `${data.sold[i]} sold, ${data.used[i]} used`);
  content += hitTargets(
    forecastCollected.map((v, i) => ({ x: xAt(historyCount + i), y: yAt(v), i })),
    (i) => `${monthLabel(forecastMonths[i])} — ${Math.round(forecastCollected[i])} forecast`);

  let divider = "";
  if (forecastMonths.length > 0) {
    // Offset by the gutter like every other x. Missed when the y axis was
    // added in 1.49.0, so this line sat one gutter-width left of the boundary
    // it marks — pointing at the wrong month.
    const dividerX = axis.gutter + historyCount * pointSpacing;
    divider = `<line x1="${dividerX}" y1="${topPad}" x2="${dividerX}" y2="${topPad + chartH}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3"></line>`;
  }

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}${divider}</svg>`;
}

// Splits a series into runs of contiguous non-null points, already
// transformed to pixels, so a stretch with no data leaves a gap in the
// line instead of one drawn straight through it. Shared by both
// eggs-per-day charts, where a gap means "no collection speaks for these
// days" and must not read as a drop to zero; each chart styles the runs
// itself. Each point keeps its index `i` in the original series, since
// that's what the caller's own per-point data is keyed by.
function splitRuns(values, xAt, yAt, offset = 0) {
  const runs = [];
  let run = [];
  values.forEach((v, i) => {
    if (v == null) {
      if (run.length) runs.push(run);
      run = [];
      return;
    }
    run.push({ x: xAt(offset + i), y: yAt(v), i });
  });
  if (run.length) runs.push(run);
  return runs;
}

// A month whose rate rests on only part of the month — typically the one
// you started logging in. Not wrong, but not comparable to a month with
// full coverage sitting next to it, which is the whole reason it's
// marked. The threshold comes from the API so there's one definition of
// it (see EGGS_PER_DAY_MIN_COVERED_DAYS).
function isThinlyCovered(data, i) {
  const days = (data.eggs_per_day_days || [])[i];
  return days != null && days > 0 && days < (data.eggs_per_day_min_days || 0);
}

// Same visual grammar as buildTrendsSvg (same spacing, same plot height,
// dashed = forecast) but its own y-scale: eggs/day lives in single digits
// where the monthly totals above live in the hundreds, so sharing one
// axis would flatten this line onto the floor. It also has to draw around
// gaps — a month no collection covers is null, not zero (see
// _compute_eggs_per_day) — so the line breaks there instead of diving.
function buildEggsPerDaySvg(data) {
  const pointSpacing = 48;
  const chartH = 120;
  const topPad = 10;
  const labelH = 16;
  const history = data.eggs_per_day || [];
  const forecast = data.forecast_eggs_per_day || [];
  const forecastMonths = data.forecast_months || [];
  const historyCount = data.months.length;
  const totalCount = historyCount + forecastMonths.length;
  const plotW = totalCount * pointSpacing;
  const height = topPad + chartH + labelH;
  const birds = data.birds || 0;
  const maxVal = Math.max(1, birds, ...history.filter((v) => v != null), ...forecast);

  const axis = chartYAxis(maxVal, "eggs/day", { topPad, chartH });
  // The gutter is added beside the plot, never taken out of it: an axis that
  // narrowed the chart would redraw the data every time a label got wider.
  const width = axis.gutter + plotW;
  const xAt = (i) => axis.gutter + i * pointSpacing + pointSpacing / 2;
  const yAt = (value) => topPad + chartH - (value / maxVal) * chartH;

  const line = (values, offset, colorVar, { dashed = false, opacity = 1, thin = () => false } = {}) => {
    const dash = dashed ? ' stroke-dasharray="4,3"' : "";
    const runs = splitRuns(values, xAt, yAt, offset);

    let svg = runs
      .filter((points) => points.length > 1)
      .map(
        (points) =>
          `<polyline points="${points.map((p) => `${p.x},${p.y}`).join(" ")}" fill="none" stroke="var(${colorVar})" stroke-width="2" stroke-opacity="${opacity}"${dash}></polyline>`
      )
      .join("");
    // Circles last, and drawn for lone points too — a single measured
    // month in a range of gaps has no polyline to render it. Thinly
    // covered months get a hollow marker: the rate is real, but it isn't
    // a whole month's worth and shouldn't read as one.
    runs.flat().forEach((p) => {
      svg += thin(p.i)
        ? `<circle cx="${p.x}" cy="${p.y}" r="2.5" fill="var(--surface)" stroke="var(${colorVar})" stroke-width="1.5" stroke-opacity="${opacity}"></circle>`
        : `<circle cx="${p.x}" cy="${p.y}" r="2.5" fill="var(${colorVar})" fill-opacity="${opacity}"></circle>`;
    });
    return svg;
  };

  let content = axis.render(width);
  content += flockCeiling(birds, yAt, maxVal, axis.gutter, width);
  content += line(history, 0, "--accent-egg", { thin: (i) => isThinlyCovered(data, i) });
  content += line(forecast, historyCount, "--accent-egg", { dashed: true, opacity: 0.55 });

  data.months.forEach((ym, i) => {
    content += `<text class="trends-bar-label" x="${xAt(i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });
  forecastMonths.forEach((ym, i) => {
    content += `<text class="trends-bar-label trends-bar-label-forecast" x="${xAt(historyCount + i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });

  content += hitTargets(
    history.map((v, i) => (v == null ? null : { x: xAt(i), y: yAt(v), i })),
    (i) => `${monthLabel(data.months[i])} — ${history[i].toFixed(2)} eggs/day`
           + (isThinlyCovered(data, i) ? "\nOnly part of the month is covered." : ""));
  content += hitTargets(
    forecast.map((v, i) => (v == null ? null
                            : { x: xAt(historyCount + i), y: yAt(v), i })),
    (i) => `${monthLabel(forecastMonths[i])} — ${forecast[i].toFixed(2)} eggs/day (forecast)`);

  let divider = "";
  if (forecastMonths.length > 0) {
    const dividerX = axis.gutter + historyCount * pointSpacing;
    divider = `<line x1="${dividerX}" y1="${topPad}" x2="${dividerX}" y2="${topPad + chartH}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3"></line>`;
  }

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}${divider}</svg>`;
}

function dayLabel(iso) {
  const [, month, day] = iso.split("-").map(Number);
  return `${day} ${MONTH_NAMES[month - 1].slice(0, 3)}`;
}

// The day-by-day view: same attributed rate as the monthly chart, but at
// daily resolution and ending today, for "how are they laying *now*" —
// which the monthly chart can't answer, since its last point averages
// everything since the 1st. Tighter point spacing than the monthly
// charts (30-90 points, not 6-15) and no forecast: this one is about
// what's happening, not what's coming.
function buildDailyEggsSvg(data) {
  const pointSpacing = 12;
  const chartH = 100;
  const topPad = 10;
  const labelH = 14;
  // Room for the outermost date labels, which are centred on points at
  // the very edge of the plot and would otherwise be clipped by the
  // viewBox — the monthly charts get away without it because "Jul" is a
  // third the width of "29 Jul".
  const sidePad = 14;
  const values = data.eggs_per_day || [];
  const count = values.length;
  const plotW = Math.max(count, 1) * pointSpacing + sidePad * 2;
  const height = topPad + chartH + labelH;
  // The ceiling is part of the range: with five hens laying four, an axis that
  // stopped at four would hide how much headroom there was.
  const birds = data.birds || 0;
  const maxVal = Math.max(1, birds, ...values.filter((v) => v != null));

  const axis = chartYAxis(maxVal, "eggs/day", { topPad, chartH });
  // The gutter is added beside the plot, never taken out of it: an axis that
  // narrowed the chart would redraw the data every time a label got wider.
  const width = axis.gutter + plotW;
  const xAt = (i) => axis.gutter + sidePad + i * pointSpacing + pointSpacing / 2;
  const yAt = (value) => topPad + chartH - (value / maxVal) * chartH;

  const runs = splitRuns(values, xAt, yAt);
  let content = axis.render(width);
  content += flockCeiling(birds, yAt, maxVal, axis.gutter, width);
  content += runs
    .filter((run) => run.length > 1)
    .map(
      (run) =>
        `<polyline points="${run.map((p) => `${p.x},${p.y}`).join(" ")}" fill="none" stroke="var(--accent-egg)" stroke-width="2" stroke-linejoin="round"></polyline>`
    )
    .join("");
  // No per-point markers at this density — they'd merge into a smear —
  // except for a lone covered day, which has no polyline to render it.
  runs
    .filter((run) => run.length === 1)
    .forEach((run) => {
      content += `<circle cx="${run[0].x}" cy="${run[0].y}" r="2.5" fill="var(--accent-egg)"></circle>`;
    });

  // The most recent covered day gets a marker: the line usually stops
  // short of today (eggs collected since are still in the nest), and
  // without a deliberate end point that looks like missing data rather
  // than the edge of what's known.
  const lastRun = runs[runs.length - 1];
  if (lastRun) {
    const last = lastRun[lastRun.length - 1];
    content += `<circle cx="${last.x}" cy="${last.y}" r="3.5" fill="var(--accent-egg)"></circle>`;
  }

  // A day whose rate is above the flock size. Ringed rather than hidden or
  // clamped: the figure is what the collections say, and what is wrong is the
  // assumption that each visit emptied the nest — so it is the keeper who
  // needs telling, not the number that needs changing.
  const impossible = new Set(data.impossible || []);
  runs.flat().forEach((point) => {
    if (!impossible.has(point.i)) return;
    content += `<circle class="egg-impossible" cx="${point.x}" cy="${point.y}" r="4"></circle>`;
  });

  // Labelled from the right, so today's end of the window always carries
  // a date and the intervals fall back from it.
  const labelEvery = Math.max(1, Math.round(count / 5));
  for (let i = count - 1; i >= 0; i -= labelEvery) {
    content += `<text class="trends-bar-label" x="${xAt(i)}" y="${height - 2}" text-anchor="middle">${dayLabel(data.days[i])}</text>`;
  }

  content += hitTargets(
    values.map((v, i) => (v == null ? null : { x: xAt(i), y: yAt(v), i })),
    (i) => {
      const rate = values[i];
      const base = `${dayLabel(data.days[i])} — ${rate.toFixed(2)} eggs/day`;
      const tail = impossible.has(i)
        ? `\nAbove ${birds} hens — some of these were laid earlier and found late.`
        : "";
      return `${base}${tail}\nTap to see what this rests on.`;
    },
    (i) => data.days[i]);

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}</svg>`;
}

// A separate builder rather than sharing buildTrendsSvg above: this chart
// has one actual series + one forecast line + one CI band over its own
// x-domain (all available history, not the range selector) with no
// backtest/divider concept — abstracting over that many differing knobs
// would cost more indirection than the overlap saves. Only bandPolygon
// (the primitive, not a full chart builder) is genuinely shared.
function buildAdvancedForecastSvg(data) {
  const pointSpacing = 48;
  const chartH = 120;
  const topPad = 10;
  const labelH = 16;
  const historyCount = data.months.length;
  const forecastCount = data.advanced_months.length;
  const totalCount = historyCount + forecastCount;
  const plotW = totalCount * pointSpacing;
  const height = topPad + chartH + labelH;
  const maxVal = Math.max(1, ...data.collected, ...data.advanced_ci_upper);

  const axis = chartYAxis(maxVal, "eggs", { topPad, chartH });
  // The gutter is added beside the plot, never taken out of it: an axis that
  // narrowed the chart would redraw the data every time a label got wider.
  const width = axis.gutter + plotW;
  const xAt = (i) => axis.gutter + i * pointSpacing + pointSpacing / 2;
  const yAt = (value) => topPad + chartH - (value / maxVal) * chartH;

  const line = (values, offset, colorVar, { dashed = false } = {}) => {
    const points = values.map((v, i) => `${xAt(offset + i)},${yAt(v)}`).join(" ");
    const dash = dashed ? ' stroke-dasharray="4,3"' : "";
    let svg = `<polyline points="${points}" fill="none" stroke="var(${colorVar})" stroke-width="2"${dash}></polyline>`;
    values.forEach((v, i) => {
      svg += `<circle cx="${xAt(offset + i)}" cy="${yAt(v)}" r="2.5" fill="var(${colorVar})"></circle>`;
    });
    return svg;
  };

  let content = axis.render(width);
  if (forecastCount > 0) {
    const xs = data.advanced_forecast.map((_, i) => xAt(historyCount + i));
    const ysUpper = data.advanced_ci_upper.map(yAt);
    const ysLower = data.advanced_ci_lower.map(yAt);
    content += bandPolygon(xs, ysUpper, ysLower, "--accent-egg");
  }
  content += line(data.collected, 0, "--accent-egg");
  if (forecastCount > 0) {
    content += line(data.advanced_forecast, historyCount, "--accent-egg", { dashed: true });
  }

  data.months.forEach((ym, i) => {
    content += `<text class="trends-bar-label" x="${xAt(i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });
  data.advanced_months.forEach((ym, i) => {
    content += `<text class="trends-bar-label trends-bar-label-forecast" x="${xAt(historyCount + i)}" y="${height - 2}" text-anchor="middle">${monthLabel(ym).split(" ")[0]}</text>`;
  });

  content += hitTargets(
    data.collected.map((v, i) => ({ x: xAt(i), y: yAt(v), i })),
    (i) => `${monthLabel(data.months[i])} — ${data.collected[i]} collected`);
  content += hitTargets(
    data.advanced_forecast.map((v, i) => ({ x: xAt(historyCount + i), y: yAt(v), i })),
    (i) => `${monthLabel(data.advanced_months[i])} — `
           + `${Math.round(data.advanced_forecast[i])} forecast `
           + `(${Math.round(data.advanced_ci_lower[i])}–`
           + `${Math.round(data.advanced_ci_upper[i])})`);

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}</svg>`;
}

let advancedForecastLoaded = false;

async function loadAdvancedForecast() {
  const messageEl = document.getElementById("advanced-forecast-message");
  const chartWrap = document.getElementById("advanced-forecast-chart-wrap");
  chartWrap.hidden = true;
  chartWrap.querySelector("svg")?.remove();

  let data;
  try {
    const res = await fetch("api/trends/advanced");
    data = await res.json();
  } catch (err) {
    messageEl.textContent = "Couldn't load the advanced forecast — check your connection.";
    return;
  }

  if (!data.advanced_enabled) {
    messageEl.textContent = "Enable Advanced forecast in the add-on's Configuration tab to try this.";
  } else if (!data.advanced_libs_available) {
    messageEl.textContent = "Not available on this device's architecture (requires amd64 or aarch64).";
  } else if (data.advanced_error) {
    messageEl.textContent = "Couldn't fit a model with your current data.";
  } else if (data.history_months < data.min_months_required) {
    messageEl.textContent = `Log at least ${data.min_months_required} months of egg collection to unlock this (${data.history_months} so far).`;
  } else {
    const seasonalNote =
      data.model === "holt_winters_seasonal"
        ? "This includes a data-driven seasonal component (24+ months of history)."
        : `This is a trend-only fit — log ${data.seasonal_min_months_required} months total for a seasonal component too (${data.history_months} so far).`;
    messageEl.textContent = `An independent statistical model (Holt-Winters), fitted directly on your history, as a check against the forecast above. ${seasonalNote} The shaded range is its 95% confidence interval.`;
    chartWrap.insertAdjacentHTML("beforeend", buildAdvancedForecastSvg(data));
    chartWrap.hidden = false;
  }
}

document.getElementById("advanced-forecast-panel").addEventListener("toggle", (e) => {
  if (e.target.open && !advancedForecastLoaded) {
    advancedForecastLoaded = true;
    loadAdvancedForecast();
  }
});

// The rate plus the number of days it was actually averaged over —
// without that second number, a month covering 9 days and one covering
// 30 are indistinguishable in the table, which is exactly the comparison
// that misleads.
function perDayCell(data, i) {
  const value = (data.eggs_per_day || [])[i];
  if (value == null) return "–";
  const days = (data.eggs_per_day_days || [])[i];
  const thin = isThinlyCovered(data, i);
  const title = thin
    ? ` title="Averaged over only ${days} covered days — not a full month, so it isn't comparable to the months around it."`
    : "";
  return (
    `<span class="${thin ? "eggs-per-day-thin" : ""}"${title}>${value.toFixed(1)}</span>` +
    `<span class="eggs-per-day-coverage">${days}d</span>`
  );
}

function renderEggsPerDay(data) {
  const chartWrap = document.getElementById("eggs-per-day-chart-wrap");
  const emptyEl = document.getElementById("eggs-per-day-empty");
  const captionEl = document.getElementById("eggs-per-day-caption");
  const perDay = data.eggs_per_day || [];
  const measured = perDay.filter((v) => v != null);

  chartWrap.querySelector("svg")?.remove();
  emptyEl.hidden = measured.length > 0;
  if (measured.length > 0) {
    chartWrap.insertAdjacentHTML("beforeend", buildEggsPerDaySvg(data));
  }

  let caption =
    "Average eggs laid per day. Each collection counts for every day since the one before it, so collecting every few days instead of daily doesn't drag the average down. Days since your last collection are left out until you collect them.";
  if (measured.length < perDay.length) {
    caption += " Months with no collection to go on are left blank.";
  }
  if (perDay.some((_, i) => isThinlyCovered(data, i))) {
    caption +=
      ` The small figure under each rate is how many days it was averaged over. Hollow points cover under ${data.eggs_per_day_min_days} days —` +
      " usually the month you started logging, so they read high next to a full month rather than being comparable to one.";
  }
  captionEl.textContent = caption;
}

const dailyEggsRangeSelect = document.getElementById("daily-eggs-range");

async function loadDailyEggs() {
  const chartWrap = document.getElementById("daily-eggs-chart-wrap");
  const emptyEl = document.getElementById("daily-eggs-empty");
  const captionEl = document.getElementById("daily-eggs-caption");

  let data;
  try {
    const res = await fetch(`api/trends/daily?days=${dailyEggsRangeSelect.value}`);
    data = await res.json();
  } catch (err) {
    return;
  }

  const values = data.eggs_per_day || [];
  const measured = values.filter((v) => v != null);
  chartWrap.querySelector("svg")?.remove();
  emptyEl.hidden = measured.length > 0;
  if (measured.length > 0) {
    chartWrap.insertAdjacentHTML("beforeend", buildDailyEggsSvg(data));
  }

  let caption =
    "Eggs a day up to today, on the same basis as the chart above — a collection counts for every day since the last one, so the flat stretches are days a single collection covers.";
  // The trailing gap is the one thing about this chart that looks like a
  // fault, so it's called out whenever it's actually on screen.
  let lastMeasured = -1;
  values.forEach((v, i) => {
    if (v != null) lastMeasured = i;
  });
  const trailingGap = values.length - 1 - lastMeasured;
  if (measured.length > 0 && trailingGap > 0) {
    caption +=
      ` The line stops ${trailingGap} day${trailingGap === 1 ? "" : "s"} short of today because that's your last collection —` +
      " anything laid since is still in the nest, not a drop to zero.";
  }
  captionEl.textContent = caption;
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

  renderEggsPerDay(data);

  const backtest = data.forecast_backtest || [];
  const perDay = data.eggs_per_day || [];
  const historyRows = data.months
    .map(
      (ym, i) => `
        <tr>
          <td>${monthLabel(ym)}</td>
          <td>${data.collected[i]}</td>
          <td>${perDayCell(data, i)}</td>
          <td>${backtest[i]}</td>
          <td>${data.sold[i]}</td>
          <td>${data.used[i]}</td>
        </tr>
      `
    )
    .join("");

  const forecastPerDay = data.forecast_eggs_per_day || [];
  const forecastRows = (data.forecast_months || [])
    .map(
      (ym, i) => `
        <tr class="trends-row-forecast">
          <td>${monthLabel(ym)} (forecast)</td>
          <td>–</td>
          <td>${forecastPerDay[i] == null ? "–" : forecastPerDay[i].toFixed(1)}</td>
          <td>${data.forecast_collected[i]}</td>
          <td>–</td>
          <td>–</td>
        </tr>
      `
    )
    .join("");

  trendsTableBody.innerHTML = historyRows + forecastRows;

  const flockBasisNote =
    data.forecast_flock_basis === "individual"
      ? "your chickens' ages"
      : "flat per-breed counts — add chickens in 🐔 My Flock for an age-adjusted forecast";
  let caption =
    data.forecast_basis === "breed_standard"
      ? `The dashed line is based on breed averages for ${flockBasisNote} and the season (longer days boost laying in summer, shorter days lower it in winter) — log a few weeks of collection to refine it. It also shows what it would have predicted for past months, so you can see how it's tracking.`
      : `The dashed line is based on breed averages for ${flockBasisNote}, adjusted by your last 30 days of collection and the season (longer days boost laying in summer, shorter days lower it in winter). Past months show what it would have predicted at the time, so you can see how it's tracking.`;
  document.getElementById("trends-legend-margin").hidden = data.forecast_margin == null;
  if (data.forecast_margin != null) {
    caption += ` Actual collection has typically landed within ±${data.forecast_margin} eggs of this projection.`;
  }
  trendsForecastCaption.textContent = caption;

  loadDailyEggs();
  loadFeedingStatsSummary();
}

async function loadFeedingStatsSummary() {
  const bodyEl = document.getElementById("feeding-stats-summary-body");
  const emptyEl = document.getElementById("feeding-stats-summary-empty");
  if (!bodyEl || !emptyEl) return;

  let stats = [];
  try {
    const res = await fetch("api/feeding-stats-all");
    stats = await res.json();
  } catch (err) {
    stats = [];
  }

  emptyEl.hidden = stats.length > 0;
  bodyEl.innerHTML = stats
    .map((row) => {
      const avg = row.avg_days_between_empty != null ? `${row.avg_days_between_empty}` : "–";
      const lastEmptied =
        row.days_since_last_empty != null ? `${Math.round(row.days_since_last_empty)}d ago` : "Never";
      return `
        <tr>
          <td>${escapeHtml(row.food_type)}</td>
          <td>${avg}</td>
          <td>${lastEmptied}</td>
          <td>${row.total_feedings}</td>
        </tr>
      `;
    })
    .join("");
}

function switchTab(pageId) {
  document.querySelectorAll(".page").forEach((page) => {
    page.hidden = page.id !== pageId;
  });
  tabButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.page === pageId));
  if (pageId !== "page-trends") setTrendsFullscreen(false);
  if (pageId === "page-trends") loadTrends();
  // Batches age by the clock alone, so a tab opened an hour later is stale
  // without anything having happened. Cheap enough to refetch on arrival.
  if (pageId === "page-ferment") loadFerment();
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.page));
});

trendsRangeSelect.addEventListener("change", loadTrends);
dailyEggsRangeSelect.addEventListener("change", loadDailyEggs);

function formatChickenAge(hatchDate) {
  if (!hatchDate) return "Unknown age";
  const days = Math.floor((Date.now() - new Date(hatchDate).getTime()) / 86400000);
  if (days < 30) return `${days} day${days === 1 ? "" : "s"} old`;
  if (days < 365) {
    const months = Math.floor(days / 30);
    return `${months} month${months === 1 ? "" : "s"} old`;
  }
  const years = Math.floor(days / 365);
  const months = Math.floor((days % 365) / 30);
  return months > 0 ? `${years}y ${months}mo old` : `${years} year${years === 1 ? "" : "s"} old`;
}

async function loadBreedDropdownOptions(selectEl, currentValue = null) {
  try {
    const res = await fetch("api/breeds");
    const breeds = await res.json();
    selectEl.innerHTML =
      '<option value="">No breed set</option>' +
      breeds.map((b) => `<option value="${escapeHtml(b.name)}">${escapeHtml(b.name)}</option>`).join("");
  } catch (err) {
    selectEl.innerHTML = '<option value="">No breed set</option>';
  }
  if (currentValue) {
    const hasOption = Array.from(selectEl.options).some((opt) => opt.value === currentValue);
    if (!hasOption) {
      // Preserves a breed that was removed from the list after this bird
      // was assigned it, instead of silently reassigning it to "No breed
      // set" — same reasoning as ensureFoodTypeOption() above.
      const opt = document.createElement("option");
      opt.value = currentValue;
      opt.textContent = currentValue;
      selectEl.appendChild(opt);
    }
    selectEl.value = currentValue;
  }
}

async function loadBreedList() {
  const listEl = document.getElementById("breed-list");
  try {
    const res = await fetch("api/breeds");
    const breeds = await res.json();
    listEl.innerHTML = breeds
      .map(
        (b) => `
          <li>
            <span>${escapeHtml(b.name)} <span class="breed-annual-eggs">(${b.annual_eggs}/yr)</span></span>
            <button type="button" class="food-type-delete-btn breed-delete-btn" data-id="${b.id}" aria-label="Remove ${escapeHtml(b.name)}">✕</button>
          </li>
        `
      )
      .join("");
  } catch (err) {
    listEl.innerHTML = "<li>Could not load breeds.</li>";
  }
}

let chickenCache = {};
let pendingPhotoDataUri; // undefined = no change; a data URI = new photo; null = explicitly removed

function resizeImageToDataUri(file, maxDim = 400, quality = 0.7) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        let { width, height } = img;
        if (width > height && width > maxDim) {
          height = Math.round((height * maxDim) / width);
          width = maxDim;
        } else if (height > maxDim) {
          width = Math.round((width * maxDim) / height);
          height = maxDim;
        }
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        canvas.getContext("2d").drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL("image/jpeg", quality));
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function loadChickenList() {
  const listEl = document.getElementById("chicken-list");
  const emptyEl = document.getElementById("chicken-list-empty");
  let chickens = [];
  try {
    const res = await fetch("api/chickens");
    chickens = await res.json();
  } catch (err) {
    chickens = [];
  }

  chickenCache = {};
  chickens.forEach((c) => {
    chickenCache[c.id] = c;
  });

  emptyEl.hidden = chickens.length > 0;
  listEl.innerHTML = chickens
    .map(
      (c) => `
        <li class="chicken-item" data-id="${c.id}">
          ${
            c.has_photo
              ? `<img class="chicken-avatar" src="api/chickens/${c.id}/photo" alt="">`
              : `<span class="chicken-avatar chicken-avatar-placeholder">🐔</span>`
          }
          <div class="details">
            <div class="title">${escapeHtml(c.name)}${c.status === "lost" ? " (lost)" : ""}</div>
            <div class="meta">${escapeHtml(c.breed || "No breed set")} · ${formatChickenAge(c.hatch_date)}</div>
          </div>
          <button type="button" class="food-type-delete-btn chicken-delete-btn" data-id="${c.id}" aria-label="Remove ${escapeHtml(c.name)}">✕</button>
        </li>
      `
    )
    .join("");
}

function openChickenForm(chicken = null) {
  const formEl = document.getElementById("chicken-form");
  document.getElementById("chicken-form-id").value = chicken ? chicken.id : "";
  document.getElementById("chicken-form-name").value = chicken ? chicken.name : "";
  document.getElementById("chicken-form-hatch-date").value = chicken ? chicken.hatch_date || "" : "";
  document.getElementById("chicken-form-status").value = chicken ? chicken.status : "active";
  loadBreedDropdownOptions(document.getElementById("chicken-form-breed"), chicken ? chicken.breed : null);

  pendingPhotoDataUri = undefined;
  document.getElementById("chicken-form-photo-input").value = "";
  const previewEl = document.getElementById("chicken-form-photo-preview");
  const removePhotoBtn = document.getElementById("chicken-form-remove-photo-btn");
  if (chicken && chicken.has_photo) {
    previewEl.src = `api/chickens/${chicken.id}/photo`;
    previewEl.hidden = false;
    removePhotoBtn.hidden = false;
  } else {
    previewEl.hidden = true;
    removePhotoBtn.hidden = true;
  }

  // Health history only exists for an already-saved chicken — a new one
  // has no id to attach events to yet.
  healthChickenId = chicken ? chicken.id : null;
  document.getElementById("chicken-health-section").hidden = !chicken;
  document.getElementById("health-add-form").hidden = true;
  if (chicken) loadHealthEvents(chicken.id);

  formEl.hidden = false;
}

function closeChickenForm() {
  document.getElementById("chicken-form").hidden = true;
}

// --- Health history (inside the chicken edit form) ---

let healthChickenId = null;

const HEALTH_EVENT_LABELS = {
  vet_visit: "Vet visit",
  vaccination: "Vaccination",
  molt_start: "Molt started",
  molt_end: "Molt ended",
  weight: "Weight check",
  observation: "Observation",
};

async function loadHealthEvents(chickenId) {
  const listEl = document.getElementById("health-event-list");
  const emptyEl = document.getElementById("health-event-empty");
  let events = [];
  try {
    const res = await fetch(`api/chickens/${chickenId}/health`);
    events = await res.json();
  } catch (err) {
    events = [];
  }

  emptyEl.hidden = events.length > 0;
  listEl.innerHTML = events
    .map((e) => {
      const weight = e.weight_grams != null ? ` · ${e.weight_grams} g` : "";
      const notes = e.notes ? ` · ${escapeHtml(e.notes)}` : "";
      return `
        <li>
          <span>${HEALTH_EVENT_LABELS[e.event_type] || e.event_type} · ${e.event_date}${weight}${notes}</span>
          <button type="button" class="food-type-delete-btn health-event-delete-btn" data-id="${e.id}" aria-label="Delete event">✕</button>
        </li>
      `;
    })
    .join("");
}

document.getElementById("health-add-btn").addEventListener("click", () => {
  const formEl = document.getElementById("health-add-form");
  formEl.hidden = !formEl.hidden;
  if (!formEl.hidden) {
    document.getElementById("health-form-date").value = new Date().toISOString().slice(0, 10);
    document.getElementById("health-form-notes").value = "";
    document.getElementById("health-form-weight").value = "";
  }
});

document.getElementById("health-form-type").addEventListener("change", (e) => {
  document.getElementById("health-form-weight-field").hidden = e.target.value !== "weight";
});

document.getElementById("health-form-cancel-btn").addEventListener("click", () => {
  document.getElementById("health-add-form").hidden = true;
});

document.getElementById("health-form-save-btn").addEventListener("click", async () => {
  if (!healthChickenId) return;
  const eventType = document.getElementById("health-form-type").value;
  const payload = {
    event_type: eventType,
    event_date: document.getElementById("health-form-date").value,
    notes: document.getElementById("health-form-notes").value.trim() || null,
  };
  const weight = document.getElementById("health-form-weight").value;
  if (weight) payload.weight_grams = Number(weight);
  if (eventType === "weight" && !weight) {
    alert("Weight is required for a weight check.");
    return;
  }

  try {
    const res = await fetch(`api/chickens/${healthChickenId}/health`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "Couldn't save that event.");
      return;
    }
    document.getElementById("health-add-form").hidden = true;
    loadHealthEvents(healthChickenId);
  } catch (err) {
    alert("Couldn't save — check your connection and try again.");
  }
});

document.getElementById("health-event-list").addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".health-event-delete-btn");
  if (!deleteBtn || !healthChickenId) return;
  await fetch(`api/health-events/${deleteBtn.dataset.id}`, { method: "DELETE" });
  loadHealthEvents(healthChickenId);
});

document.getElementById("chicken-add-btn").addEventListener("click", () => openChickenForm(null));
document.getElementById("chicken-form-cancel-btn").addEventListener("click", closeChickenForm);

document.getElementById("chicken-form-photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const dataUri = await resizeImageToDataUri(file);
  pendingPhotoDataUri = dataUri;
  const previewEl = document.getElementById("chicken-form-photo-preview");
  previewEl.src = dataUri;
  previewEl.hidden = false;
  document.getElementById("chicken-form-remove-photo-btn").hidden = false;
});

document.getElementById("chicken-form-remove-photo-btn").addEventListener("click", () => {
  pendingPhotoDataUri = null;
  document.getElementById("chicken-form-photo-input").value = "";
  document.getElementById("chicken-form-photo-preview").hidden = true;
  document.getElementById("chicken-form-remove-photo-btn").hidden = true;
});

document.getElementById("chicken-form-save-btn").addEventListener("click", async () => {
  const id = document.getElementById("chicken-form-id").value;
  const name = document.getElementById("chicken-form-name").value.trim();
  if (!name) {
    alert("Name is required.");
    return;
  }
  const payload = {
    name,
    breed: document.getElementById("chicken-form-breed").value || null,
    hatch_date: document.getElementById("chicken-form-hatch-date").value || null,
    status: document.getElementById("chicken-form-status").value,
  };
  if (pendingPhotoDataUri !== undefined) {
    payload.photo = pendingPhotoDataUri;
  }

  try {
    const res = id
      ? await fetch(`api/chickens/${id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
      : await fetch("api/chickens", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.error || "Couldn't save that chicken.");
      return;
    }
    closeChickenForm();
    loadChickenList();
  } catch (err) {
    alert("Couldn't save — check your connection and try again.");
  }
});

document.getElementById("chicken-list").addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".chicken-delete-btn");
  if (deleteBtn) {
    e.stopPropagation();
    if (confirm("Remove this chicken? This can't be undone.")) {
      await fetch(`api/chickens/${deleteBtn.dataset.id}`, { method: "DELETE" });
      loadChickenList();
    }
    return;
  }

  // Tapping the photo enlarges it instead of opening the edit form — only
  // real photos (an <img>), never the emoji placeholder.
  const avatar = e.target.closest("img.chicken-avatar");
  if (avatar) {
    e.stopPropagation();
    const chicken = chickenCache[e.target.closest(".chicken-item").dataset.id];
    if (chicken) openPhotoLightbox(`api/chickens/${chicken.id}/photo`, chicken.name);
    return;
  }

  const item = e.target.closest(".chicken-item");
  if (item) {
    const chicken = chickenCache[item.dataset.id];
    if (chicken) openChickenForm(chicken);
  }
});

// --- Photo lightbox (tap a chicken photo to see it full-size) ---

function openPhotoLightbox(src, caption) {
  const box = document.getElementById("photo-lightbox");
  document.getElementById("photo-lightbox-img").src = src;
  document.getElementById("photo-lightbox-caption").textContent = caption || "";
  box.hidden = false;
}

document.getElementById("photo-lightbox").addEventListener("click", () => {
  const box = document.getElementById("photo-lightbox");
  box.hidden = true;
  document.getElementById("photo-lightbox-img").src = "";
});

document.getElementById("breed-add-btn").addEventListener("click", async () => {
  const name = document.getElementById("breed-new-name").value.trim();
  const annualEggsInput = document.getElementById("breed-new-annual-eggs");
  if (!name || !annualEggsInput.value) return;

  const res = await fetch("api/breeds", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, annual_eggs: parseInt(annualEggsInput.value, 10) }),
  });
  if (res.ok) {
    document.getElementById("breed-new-name").value = "";
    annualEggsInput.value = "";
    loadBreedList();
  } else {
    const data = await res.json().catch(() => ({}));
    alert(data.error || "Couldn't add that breed.");
  }
});

document.getElementById("breed-list").addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".breed-delete-btn");
  if (!deleteBtn) return;
  await fetch(`api/breeds/${deleteBtn.dataset.id}`, { method: "DELETE" });
  loadBreedList();
});

const flockBackdrop = document.getElementById("flock-backdrop");
const flockOpenBtn = document.getElementById("flock-open-btn");
const flockCloseBtn = document.getElementById("flock-close-btn");

flockOpenBtn.addEventListener("click", () => {
  flockBackdrop.classList.add("open");
  closeChickenForm();
  loadChickenList();
  loadBreedList();
});
flockCloseBtn.addEventListener("click", () => flockBackdrop.classList.remove("open"));
flockBackdrop.addEventListener("click", (e) => {
  if (e.target === flockBackdrop) flockBackdrop.classList.remove("open");
});

const haStatusDot = document.getElementById("ha-status-dot");

async function loadHaStatus() {
  try {
    const res = await fetch("api/debug");
    const data = await res.json();
    const ok = !!data.ha_api_reachable;
    haStatusDot.classList.toggle("status-ok", ok);
    haStatusDot.classList.toggle("status-error", !ok);
    haStatusDot.title = ok
      ? "Home Assistant: connected"
      : `Home Assistant: not reachable${data.ha_api_error ? " — " + data.ha_api_error : ""}`;
  } catch (err) {
    haStatusDot.classList.remove("status-ok");
    haStatusDot.classList.add("status-error");
    haStatusDot.title = "Home Assistant: could not check status";
  }
}

haStatusDot.addEventListener("click", () => {
  notifyOpenBtn.click(); // opens the Notifications panel...
  debugList.hidden = false; // ...then expand its Debug info section, which has the detail behind this dot
  debugToggle.textContent = "Debug info ▴";
  loadDebugInfo();
});

loadHaStatus();
loadSummary();
loadHistory();

// --- Flock tonics ------------------------------------------------------------
//
// Garlic in the water, oregano in the feed. Unlike a ferment nothing goes
// mouldy if you miss one, which is exactly why it needs a reminder: it does not
// fail, it just quietly stops happening.

async function loadTonics() {
  const card = document.getElementById("tonic-card");
  let data;
  try {
    data = await fetch("api/tonics").then((r) => r.json());
  } catch (err) {
    card.hidden = true;
    return null;
  }
  if (!data.enabled) { card.hidden = true; return data; }
  card.hidden = false;
  renderTonics(data);
  return data;
}

function renderTonics(data) {
  const hint = document.getElementById("tonic-hint");
  hint.textContent = data.routines.length
    ? (data.due
        ? `${data.due} due now${data.overdue ? `, ${data.overdue} well overdue` : ""}`
        : "Nothing due — all up to date.")
    : "Nothing set up yet.";

  document.getElementById("tonic-list").innerHTML = data.routines.map((r) => {
    const when = r.never_given ? "never given"
      : r.due ? `due — last ${fmtDay(r.last_given_at)}`
      : `next ${fmtDay(r.next_due_at)}`;
    // Open while it wants doing, folded once it is done. The dose and the
    // caution are what you read as you give it, so they are on screen exactly
    // when they are needed; four routines all up to date is otherwise a card
    // full of instructions for things nobody has to act on. Still one tap
    // away, because "why am I doing this" is a fair question on a quiet day.
    return `
      <details class="tonic-row${r.overdue ? " tonic-overdue" : ""}"${
        r.due ? " open" : ""} data-id="${r.id}">
        <summary class="tonic-summary">
          <span class="ferment-main">
            <span class="ferment-name">${escapeHtml(r.name)}</span>
            <span class="ferment-meta">${escapeHtml(when)} · every ${r.cadence_days} d${
              r.doses ? ` · given ${r.doses}×` : ""}</span>
          </span>
          <span class="ferment-actions">
            <button type="button" class="btn-small" data-tonic-given="${r.id}">Given</button>
            <button type="button" class="btn-small btn-quiet" data-tonic-delete="${r.id}"
              title="Remove this routine">✕</button>
          </span>
        </summary>
        <div class="tonic-body">
          ${r.dose ? `<span class="tonic-dose">${escapeHtml(r.dose)}</span>` : ""}
          ${r.notes ? `<details class="tonic-notes"><summary>Why, and what to watch</summary>
            <p>${escapeHtml(r.notes)}</p></details>` : ""}
        </div>
      </details>`;
  }).join("");
}

document.getElementById("tonic-list").addEventListener("click", async (event) => {
  // Given and ✕ sit inside the <summary>, where a click is also the gesture
  // that folds the row. Pressing a button is not asking to collapse anything.
  if (event.target.closest("button")) event.preventDefault();

  const given = event.target.closest("[data-tonic-given]");
  if (given) {
    renderTonics(await fetch(`api/tonics/${given.dataset.tonicGiven}/given`,
                             { method: "POST" }).then((r) => r.json()));
    return;
  }
  const remove = event.target.closest("[data-tonic-delete]");
  if (!remove) return;
  if (!confirm("Remove this routine? Its history goes with it.")) return;
  renderTonics(await fetch(`api/tonics/${remove.dataset.tonicDelete}`,
                           { method: "DELETE" }).then((r) => r.json()));
});

document.getElementById("tonic-new").addEventListener("click", async () => {
  const name = prompt("What is it?\n\ne.g. Garlic in the water");
  if (!name) return;
  const dose = prompt("How much?\n\ne.g. 1 crushed clove per litre") || "";
  const cadence = prompt("How often, in days?", "7");
  const response = await fetch("api/tonics", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, dose, cadence_days: Number(cadence) || 7 }),
  });
  const body = await response.json();
  if (!response.ok) { alert(body.error || "Could not add that."); return; }
  renderTonics(body);
});

// --- Fermented feed ----------------------------------------------------------
//
// The card exists to answer one question at a glance: is anything about to go
// mouldy. Everything else on it is secondary to the stir state.

async function loadFerment() {
  // The tab *button*, not the page. A tab you can reach that turns out to be
  // empty reads as something broken; a tab that is not there reads as a
  // feature you have not turned on, which is the truth.
  const card = document.getElementById("ferment-card");
  let data = null;
  try {
    data = await fetch("api/ferment").then((r) => r.json());
  } catch (err) {
    data = null;
  }
  card.hidden = !(data && data.enabled);
  if (data && data.enabled) renderFerment(data);

  // The tab carries two independent cards now, so it is shown when *either*
  // is on and hidden only when neither is. Deciding that here, after both
  // have loaded, is what keeps one feature from hiding the other's tab.
  const tonic = await loadTonics();
  const anything = (data && data.enabled) || (tonic && tonic.enabled);
  const tab = document.getElementById("tab-ferment-btn");
  tab.hidden = !anything;
  // Turning the last one off while somebody is standing on the page would
  // otherwise leave them on a tab with no way back to it in the bar.
  if (!anything && !document.getElementById("page-ferment").hidden) switchTab("page-home");
}

function renderFerment(data) {
  const hint = document.getElementById("ferment-hint");
  hint.textContent = data.open
    ? `${data.open} going${data.ready ? `, ${data.ready} ready to feed` : ""}`
      + `${data.stir_due ? ` · ${data.stir_due} needs stirring` : ""}`
      + `${data.spent ? ` · ${data.spent} past ${data.max_age_days} days, bin it` : ""}`
    // Nothing on the go: say what a batch for this flock would take, since
    // that is the number you need before you can start one.
    : `Nothing fermenting. A ${data.ferment_days}-day batch for ${data.birds} `
      + `hens is about ${data.suggested_grams} g of dry feed.`;

  renderStarter(data);

  document.getElementById("ferment-batches").innerHTML = data.batches.map((b) => {
    const since = b.hours_since_stir == null ? "—"
      : b.hours_since_stir < 1 ? "just now"
      : `${Math.round(b.hours_since_stir)}h ago`;
    // Where it is in its life, in the words you would use out loud. A spent tub
    // looks exactly like a good one, so the row has to carry the judgement.
    const day = Math.floor(b.age_days);
    const stage = b.spent ? `Past it — day ${day} of ${data.max_age_days}`
      : b.state === "ready" ? `Ready · day ${day} of ${data.max_age_days}`
      : `Ready ${fmtDay(b.ready_at)}`;
    return `
      <div class="ferment-row${b.stir_due ? " stir-due" : ""}${b.spent ? " batch-spent" : ""}"
        data-id="${b.id}">
        <div class="ferment-main">
          <span class="ferment-name">${escapeHtml(b.container)}</span>
          <span class="ferment-meta">
            ${stage}
            · stirred ${since}${b.grams ? ` · ${Math.round(b.grams)} g` : ""}`
    + `${b.generation ? ` · seeded (gen ${b.generation})` : ""}</span>
          <div class="stir-log" id="stir-log-${b.id}" hidden></div>
        </div>
        <div class="ferment-actions">
          ${b.stirs ? `<button type="button" class="btn-small btn-quiet"
            data-stir-log="${b.id}" title="When it was stirred">${b.stirs}×</button>` : ""}
          ${b.spent ? "" : `<button type="button" class="btn-small" data-stir="${b.id}">Stirred</button>`}
          ${b.state === "ready"
            ? `<button type="button" class="btn-small" data-close="${b.id}" data-outcome="fed">Fed</button>`
            : ""}
          <button type="button" class="btn-small${b.spent ? "" : " btn-quiet"}" data-close="${b.id}"
            data-outcome="discarded" title="Threw it away">Binned</button>
        </div>
      </div>`;
  }).join("");
}

// The jar of saved brine. Its whole reason for being on the card is that it is
// the thing you forget: it lives in the fridge rather than next to the tubs, and
// an unused culture quietly goes flat.
function renderStarter(data) {
  const box = document.getElementById("ferment-starter");
  const jar = data.starter;
  if (!jar) {
    box.hidden = false;
    box.className = "ferment-starter";
    box.innerHTML = `<span class="ferment-starter-text">No saved liquid. `
      + `Keep the brine next time you feed and the batch after that is ready `
      + `in ${data.seeded_days} days.</span>`;
    return;
  }
  const age = jar.age_days < 1 ? "today"
    : jar.age_days < 2 ? "yesterday"
    : `${Math.round(jar.age_days)} days ago`;
  // Two different cautions, and they are not the same kind of thing: stale is
  // about this jar, generations is about the line it came from. Show the one
  // that would change what you do first.
  const warning = jar.stale
    ? `<span class="ferment-warn">Saved ${age} — past ${data.starter_good_for_days} `
      + `days it may have gone quiet. Starting fresh is safer.</span>`
    : jar.many_generations
    ? `<span class="ferment-warn">Generation ${jar.generation} — worth starting `
      + `a clean batch soon.</span>`
    : "";
  box.hidden = false;
  box.className = `ferment-starter${jar.stale ? " starter-stale" : ""}`;
  box.innerHTML = `
    <span class="ferment-starter-text">🫙 Saved liquid ${age}`
    + `${jar.generation ? ` · generation ${jar.generation}` : ""}. `
    + `Seeds a ${data.seeded_days}-day batch.</span>
    ${warning}
    <button type="button" class="btn-small btn-quiet" id="starter-discard">Discard jar</button>`;
}

document.getElementById("ferment-starter").addEventListener("click", async (event) => {
  if (!event.target.closest("#starter-discard")) return;
  if (!confirm("Throw out the saved liquid? The next batch starts from scratch.")) return;
  renderFerment(await fetch("api/ferment/starter", { method: "DELETE" })
    .then((r) => r.json()));
});

function fmtDay(iso) {
  if (!iso) return "—";
  const when = new Date(iso);
  return isNaN(when) ? "—"
    : when.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

// The stirs themselves, on demand. Not loaded with the card: a batch stirred
// twice a day for a week is fourteen rows nobody is looking at until they are.
async function toggleStirLog(batchId) {
  const host = document.getElementById(`stir-log-${batchId}`);
  if (!host) return;
  if (!host.hidden) { host.hidden = true; return; }

  host.hidden = false;
  host.innerHTML = '<span class="ferment-meta">Loading…</span>';
  let data;
  try {
    data = await fetch(`api/ferment/stirs?batch=${batchId}`).then((r) => r.json());
  } catch (err) {
    host.innerHTML = '<span class="ferment-meta">Could not load the stirs.</span>';
    return;
  }

  const s = data.summary;
  const heading = s.stirs
    ? `${s.stirs} stir${s.stirs === 1 ? "" : "s"}`
      + (s.typical_gap_hours != null ? ` · usually ${s.typical_gap_hours}h apart` : "")
      + (s.late ? ` · ${s.late} late` : "")
    : "Not stirred yet.";

  host.innerHTML = `<div class="stir-log-head">${escapeHtml(heading)}</div>`
    + data.stirs.map((entry) => {
      const when = new Date(entry.stirred_at);
      const stamp = isNaN(when) ? entry.stirred_at
        : `${fmtDay(entry.stirred_at)} ${String(when.getHours()).padStart(2, "0")}:`
          + `${String(when.getMinutes()).padStart(2, "0")}`;
      // The first stir of a batch is the mixing — there is no earlier stir for
      // it to be late after, so it gets no gap rather than a zero.
      const gap = entry.first ? "mixed"
        : `${entry.hours_since_previous}h later`;
      return `<div class="stir-row${entry.late ? " stir-late" : ""}">`
        + `<span>${escapeHtml(stamp)}</span>`
        + `<span class="stir-gap">${escapeHtml(gap)}</span></div>`;
    }).join("");
}

document.getElementById("ferment-batches").addEventListener("click", async (event) => {
  const log = event.target.closest("[data-stir-log]");
  if (log) { toggleStirLog(log.dataset.stirLog); return; }
  const stir = event.target.closest("[data-stir]");
  if (stir) {
    renderFerment(await fetch(`api/ferment/batches/${stir.dataset.stir}/stir`,
                              { method: "POST" }).then((r) => r.json()));
    return;
  }
  const close = event.target.closest("[data-close][data-outcome]");
  if (!close) return;
  if (close.dataset.outcome === "discarded"
      && !confirm("Throw this batch away? It will be recorded as binned.")) return;
  // Only ever asked on a fed batch. Liquid from a binned one is the culture you
  // are binning it to be rid of, so the question is not offered there at all.
  const saveLiquid = close.dataset.outcome === "fed" && confirm(
    "Keep the liquid to start the next batch?\n\n"
    + "Drain the brine into a jar, bin the wet grain, rinse the tub. "
    + "The next batch is then ready in two days instead of three.");
  renderFerment(await fetch(`api/ferment/batches/${close.dataset.close}/close`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outcome: close.dataset.outcome, save_liquid: saveLiquid }),
  }).then((r) => r.json()));
});

document.getElementById("ferment-new").addEventListener("click", async () => {
  const current = await fetch("api/ferment").then((r) => r.json());
  const container = prompt("Which container?", `Tub ${current.open + 1}`);
  if (!container) return;
  const grams = prompt(
    `How much dry feed, in grams?\n\nAbout ${current.suggested_grams} g covers `
    + `${current.birds} hens for ${current.ferment_days} days.`,
    current.suggested_grams);
  // Offered rather than assumed: the jar might be in the fridge and the keeper
  // might still want a clean start, and only they can see it.
  const useStarter = !!current.starter && confirm(
    `Seed it with the saved liquid?\n\nStir 1-2 cups of the brine into the new `
    + `grain and water. Ready in ${current.seeded_days} days instead of `
    + `${current.ferment_days}.`);
  const response = await fetch("api/ferment/batches", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ container, grams, use_starter: useStarter }),
  });
  const body = await response.json();
  if (!response.ok) { alert(body.error || "Could not start that batch."); return; }
  renderFerment(body);
});

// --- chart hover --------------------------------------------------------------
//
// A real tooltip rather than the browser's <title> one, which waits about a
// second, is styled by the OS, and does nothing whatever on a touchscreen. Gym
// Tracker's weight chart already worked this way; this is the same idea for the
// charts here, which are built as SVG strings rather than assembled node by
// node.
//
// Delegated from the document, so it covers every chart including ones that
// re-render on a timer, and needs no wiring at the call site.

const chartTip = {
  el: null,
  point: null,
  node() {
    if (!this.el) {
      this.el = document.createElement("div");
      this.el.className = "chart-tip";
      this.el.hidden = true;
      document.body.appendChild(this.el);
    }
    return this.el;
  },
  show(hit, text) {
    const tip = this.node();
    tip.textContent = text;
    tip.hidden = false;
    if (this.point && this.point !== hit) this.point.classList.remove("is-on");
    hit.classList.add("is-on");
    this.point = hit;

    // Placed above the point and clamped to the viewport, so a tooltip near
    // the top or the right edge stays readable instead of being cut off.
    const box = hit.getBoundingClientRect();
    const size = tip.getBoundingClientRect();
    const left = Math.min(
      Math.max(6, box.left + box.width / 2 - size.width / 2),
      window.innerWidth - size.width - 6);
    const above = box.top - size.height - 8;
    tip.style.left = `${left}px`;
    tip.style.top = `${above > 6 ? above : box.bottom + 8}px`;
  },
  hide() {
    if (this.point) this.point.classList.remove("is-on");
    this.point = null;
    if (this.el) this.el.hidden = true;
  },
};

// Nearest by horizontal distance rather than whatever is directly under the
// cursor: on a dense chart the points are a few pixels apart and requiring a
// direct hit makes the tooltip flicker in and out as you move along the line.
function nearestChartHit(svg, clientX) {
  let best = null;
  let bestDistance = Infinity;
  for (const hit of svg.querySelectorAll(".chart-hit")) {
    const box = hit.getBoundingClientRect();
    const distance = Math.abs(box.left + box.width / 2 - clientX);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = hit;
    }
  }
  return bestDistance <= 44 ? best : null;
}

function handleChartPointer(event) {
  const svg = event.target.closest("svg");
  if (!svg || !svg.querySelector(".chart-hit")) {
    chartTip.hide();
    return;
  }
  const hit = nearestChartHit(svg, event.clientX);
  const text = hit && hit.dataset.tip;
  if (text) chartTip.show(hit, text);
  else chartTip.hide();
}

document.addEventListener("pointermove", handleChartPointer);
// Touch: a tap shows it, and a tap anywhere else puts it away. Without the
// second half the tooltip would sit there until the next chart was touched.
document.addEventListener("pointerdown", handleChartPointer);
document.addEventListener("pointerleave", () => chartTip.hide());
window.addEventListener("scroll", () => chartTip.hide(), { passive: true });


// --- drilling into a day ------------------------------------------------------
//
// Clicking a point on the eggs chart asks what produced it. Worth having
// because the figure is an attributed rate rather than a count: the eggs
// credited to a day usually arrive in a collection made later and spread back
// over the days since the previous visit. That is exactly what lets a rate
// exceed the flock size, so the question "how can five hens have laid six" has
// an answer, and it is here rather than in the number.

const dayBackdrop = document.getElementById("day-backdrop");

function closeDaySheet() { dayBackdrop.classList.remove("open"); }
document.getElementById("day-close").addEventListener("click", closeDaySheet);
dayBackdrop.addEventListener("click", (event) => {
  if (event.target === dayBackdrop) closeDaySheet();
});

function entryLine(entry) {
  const time = new Date(entry.ts).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit" });
  const what = entry.count != null ? `${entry.count} ` : "";
  return `<li><span class="day-entry-what">${escapeHtml(what)}${escapeHtml(entry.type)}</span>`
    + `<span class="day-entry-when">${escapeHtml(time)}</span>`
    + (entry.notes ? `<span class="day-entry-note">${escapeHtml(entry.notes)}</span>` : "")
    + "</li>";
}

async function openDaySheet(isoDay) {
  let data;
  try {
    data = await fetch(`api/trends/day?date=${encodeURIComponent(isoDay)}`)
      .then((r) => r.json());
  } catch (err) {
    return;
  }
  document.getElementById("day-title").textContent = dayLabel(data.day);

  const parts = [];
  if (!data.covered) {
    // A break in the line, not a day of no eggs. Saying which is the
    // difference between "the hens stopped" and "nobody has been out yet".
    parts.push('<p class="muted">No collection covers this day, so the chart '
      + "shows a gap rather than a zero. Either it is before the first log, or "
      + "after the most recent one and those eggs are still in the nest.</p>");
  } else {
    const src = data.source;
    parts.push(`<p class="day-rate"><strong>${data.rate.toFixed(2)}</strong> eggs/day</p>`);

    const sameDay = src.collected_on === data.day;
    parts.push('<p class="muted">'
      + (sameDay
        ? `${src.collected} collected on this day`
        : `From ${src.collected} collected on ${escapeHtml(dayLabel(src.collected_on))}`)
      + (src.span_days > 1
        ? `, spread over the ${src.span_days} days since the previous collection.`
        : ", the day after the previous collection.")
      + "</p>");

    if (data.impossible) {
      parts.push('<p class="day-warn">That is more than '
        + `${data.birds} hens can lay in a day. The spreading assumes each visit `
        + "empties the nest, so eggs missed on one visit and found on the next "
        + "are all credited to the shorter gap. The count is right; the day it "
        + "lands on is not.</p>");
    }
    if (src.capped) {
      parts.push('<p class="muted">The gap before this collection was longer '
        + `than ${src.span_days} days, so the spread was capped there.</p>`);
    }
    if (src.entries && src.entries.length) {
      parts.push("<h3>Logged on " + escapeHtml(dayLabel(src.collected_on)) + "</h3>"
        + `<ul class="day-entries">${src.entries.map(entryLine).join("")}</ul>`);
    }
  }

  parts.push("<h3>Logged on this day</h3>");
  parts.push(data.entries.length
    ? `<ul class="day-entries">${data.entries.map(entryLine).join("")}</ul>`
    : '<p class="muted">Nothing logged on this day.</p>');

  document.getElementById("day-body").innerHTML = parts.join("");
  dayBackdrop.classList.add("open");
}

// Delegated, like the tooltip, so it covers charts that re-render on a timer.
// Only the day-resolution chart drills down: a month's point is an average of
// thirty collections and there is no single entry behind it to show.
document.addEventListener("click", (event) => {
  const hit = event.target.closest(".chart-hit[data-day]");
  if (!hit) return;
  openDaySheet(hit.dataset.day);
});


// --- scanning a receipt -------------------------------------------------------
//
// Offers, never records. A photographed till receipt is creased, thermal and
// half in shadow; the reading goes into the form for a human to confirm, and
// the alternatives it also found sit underneath as chips, because the first
// guess is wrong often enough to want the second one a tap away rather than a
// retaken photograph.

let receiptScanSupported = null;

async function receiptScanningAvailable() {
  if (receiptScanSupported !== null) return receiptScanSupported;
  try {
    const debug = await fetch("api/debug").then((r) => r.json());
    receiptScanSupported = Boolean(debug.tesseract_available && debug.opencv_available);
  } catch (err) {
    receiptScanSupported = false;
  }
  return receiptScanSupported;
}

async function revealReceiptScan() {
  const block = document.getElementById("receipt-scan");
  if (!block) return;
  // Hidden rather than shown-and-apologised-for where the engine is absent:
  // armv7 gets no OpenCV and no Tesseract, and a button that can only ever
  // return "not available on this architecture" is worse than no button.
  block.hidden = !(await receiptScanningAvailable());
}

function receiptNote(text, kind) {
  const note = document.getElementById("receipt-note");
  if (!note) return;
  note.textContent = text;
  note.className = `receipt-note${kind ? ` receipt-${kind}` : ""}`;
  note.hidden = !text;
}

function applyReceipt(found) {
  const form = document.getElementById("sheet-form");
  if (found.amount != null) form.elements.cost.value = found.amount;
  if (found.vendor && !form.elements.notes.value) {
    form.elements.notes.value = found.vendor;
  }
  // The date field is a datetime-local; the receipt only knows the day, so the
  // time of the original entry is left alone rather than reset to midnight.
  if (found.date && form.elements.ts) {
    const current = form.elements.ts.value;
    const time = current.includes("T") ? current.split("T")[1] : "12:00";
    form.elements.ts.value = `${found.date}T${time}`;
  }

  const choices = document.getElementById("receipt-choices");
  const others = (found.amounts || []).filter((a) => a !== found.amount);
  choices.hidden = others.length === 0;
  choices.innerHTML = others.length
    ? `<span class="receipt-choices-label">Or:</span>` + others
        .map((a) => `<button type="button" class="chip" data-amount="${a}">${a.toFixed(2)}</button>`)
        .join("")
    : "";
}

document.addEventListener("click", (event) => {
  const chip = event.target.closest("#receipt-choices [data-amount]");
  if (!chip) return;
  document.getElementById("sheet-form").elements.cost.value = chip.dataset.amount;
  receiptNote("Using the amount you picked.", "ok");
});

document.addEventListener("click", (event) => {
  if (!event.target.closest("#receipt-btn")) return;
  document.getElementById("receipt-file").click();
});

document.addEventListener("change", async (event) => {
  if (event.target.id !== "receipt-file") return;
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  receiptNote("Reading…");

  // Resized before upload, like the egg photos: a modern phone's 12MP JPEG is
  // several megabytes of detail Tesseract does not use, and the upload is over
  // somebody's home wifi.
  const dataUri = await shrinkImage(file, 1600);
  event.target.value = "";
  let body;
  try {
    const response = await fetch("api/expenses/scan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photo: dataUri }),
    });
    body = await response.json();
    if (!response.ok) { receiptNote(body.error || "Could not read that.", "warn"); return; }
  } catch (err) {
    receiptNote("Could not read that.", "warn");
    return;
  }

  if (!body.found_anything) {
    receiptNote("No amount found. Try a straighter photo in better light, "
                + "or just type it in.", "warn");
    return;
  }
  applyReceipt(body);
  receiptNote("Filled in from the photo — check it before saving.", "ok");
});

// Canvas resize, returning a data URI. Long edge capped; aspect kept.
function shrinkImage(file, maxEdge) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", 0.9));
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
