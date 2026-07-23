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

  document.getElementById("stat-savings-month").textContent = fmtMoney(data.savings_month);
  document.getElementById("stat-savings-total").textContent = fmtMoney(data.savings_total);

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
// A photo is analyzed once server-side (POST /api/vision/eggs, classical
// OpenCV — see ARCHITECTURE.md §20) and never stored; only the reviewed,
// user-corrected count/sizes end up logged. Coin position/size can always
// be dragged into place here, since auto-detection is a best guess, never
// authoritative — a missed/wrong coin must never block logging.

// Mirrors app.py's EGG_SIZE_MM_BOUNDS/_egg_size_code exactly, so dragging
// the coin recomputes every egg's size instantly, client-side, with no
// extra round trip to the server.
const EGG_SIZE_MM_BOUNDS = [41.5, 44.0, 46.5];
function eggSizeCode(widthMm) {
  const [sM, mL, lXl] = EGG_SIZE_MM_BOUNDS;
  if (widthMm < sM) return "S";
  if (widthMm < mL) return "M";
  if (widthMm < lXl) return "L";
  return "XL";
}
const EGG_SIZE_COLORS = { S: "#8aa9c9", M: "#6fb37a", L: "#d9a441", XL: "#c05d5d" };
const EGG_SIZE_CYCLE = ["S", "M", "L", "XL"];

const eggVisionBackdrop = document.getElementById("egg-vision-backdrop");
const eggVisionCanvasWrap = document.getElementById("egg-vision-canvas-wrap");
const eggVisionPhotoImg = document.getElementById("egg-vision-photo");
const eggVisionOverlay = document.getElementById("egg-vision-overlay");
const eggVisionStatusMsg = document.getElementById("egg-vision-status-msg");
const eggVisionChips = document.getElementById("egg-vision-chips");
const eggVisionUseBtn = document.getElementById("egg-vision-use-btn");

let eggVisionState = null; // { imageWidth, imageHeight, coinDiameterMm, coin: {cx,cy,r}, eggs: [{cx,cy,widthPx,heightPx,angle,size,manuallySet}] }
let eggVisionDrag = null; // { kind: "coin"|"egg", index, mode: "move"|"resize" }

async function startEggVisionReview(file) {
  eggVisionUseBtn.disabled = true;
  eggVisionCanvasWrap.hidden = true;
  eggVisionChips.innerHTML = "";
  eggVisionStatusMsg.textContent = "Analyzing…";
  eggVisionBackdrop.classList.add("open");

  let dataUri;
  try {
    dataUri = await resizeImageToDataUri(file, 1600, 0.85);
  } catch (err) {
    eggVisionStatusMsg.textContent = "Couldn't read that photo — try again.";
    return;
  }

  let body;
  try {
    const res = await fetch("api/vision/eggs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ photo: dataUri }),
    });
    body = await res.json();
  } catch (err) {
    eggVisionStatusMsg.textContent = "Couldn't reach the server — check your connection.";
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
    imageWidth: body.image_width,
    imageHeight: body.image_height,
    coinDiameterMm: body.coin_diameter_mm || window.EGG_VISION.coinDiameterMm,
    coin: body.coin
      ? { cx: body.coin.cx, cy: body.coin.cy, r: body.coin.r }
      : { cx: body.image_width / 2, cy: body.image_height / 2, r: Math.min(body.image_width, body.image_height) * 0.06 },
    eggs: body.eggs.map((e) => ({
      cx: e.cx,
      cy: e.cy,
      widthPx: e.width_px,
      heightPx: e.height_px,
      angle: e.angle,
      size: e.size || "M",
      manuallySet: false,
    })),
  };

  eggVisionPhotoImg.src = dataUri;
  eggVisionPhotoImg.onload = () => {
    eggVisionCanvasWrap.hidden = false;
    recomputeEggSizes();
    drawEggVisionOverlay();
    renderEggVisionChips();
  };

  eggVisionStatusMsg.textContent =
    body.status === "coin_not_found"
      ? "Couldn't find a reference coin automatically — drag the circle onto your coin (center to move, edge to resize)."
      : body.status === "no_eggs_found"
      ? "Couldn't detect any eggs — you can still log a count manually below, or use + Add egg."
      : "Drag the circle if it isn't on your coin. Tap a size chip to correct it.";
  eggVisionUseBtn.disabled = false;
}

function recomputeEggSizes() {
  if (!eggVisionState || !eggVisionState.coin || eggVisionState.coin.r <= 0) return;
  const pxPerMm = (2 * eggVisionState.coin.r) / eggVisionState.coinDiameterMm;
  eggVisionState.eggs.forEach((egg) => {
    if (egg.manuallySet) return;
    egg.size = eggSizeCode(egg.widthPx / pxPerMm);
  });
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

  const coin = eggVisionState.coin;
  ctx.strokeStyle = "#2b6cb0";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(coin.cx * scale, coin.cy * scale, coin.r * scale, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.fillStyle = "#2b6cb0";
  ctx.beginPath();
  ctx.arc(coin.cx * scale, coin.cy * scale, 3, 0, 2 * Math.PI);
  ctx.fill();
  // edge handle, at the 3-o'clock point of the circle
  ctx.beginPath();
  ctx.arc((coin.cx + coin.r) * scale, coin.cy * scale, 5, 0, 2 * Math.PI);
  ctx.fill();
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
    drawEggVisionOverlay();
    renderEggVisionChips();
    return;
  }
  const chip = e.target.closest(".egg-chip");
  if (chip) {
    const egg = eggVisionState.eggs[Number(chip.dataset.idx)];
    egg.size = EGG_SIZE_CYCLE[(EGG_SIZE_CYCLE.indexOf(egg.size) + 1) % EGG_SIZE_CYCLE.length];
    egg.manuallySet = true;
    drawEggVisionOverlay();
    renderEggVisionChips();
  }
});

document.getElementById("egg-vision-add-egg").addEventListener("click", () => {
  if (!eggVisionState) return;
  eggVisionState.eggs.push({
    cx: eggVisionState.imageWidth / 2,
    cy: eggVisionState.imageHeight / 2,
    widthPx: eggVisionState.coin.r * 3,
    heightPx: eggVisionState.coin.r * 4,
    angle: 0,
    size: "M",
    manuallySet: true,
  });
  drawEggVisionOverlay();
  renderEggVisionChips();
});

// Drag: near the coin's center moves it, near its edge resizes it; near an
// egg's center moves that egg (the only way to place a "+ Add egg" marker
// on the actual missed egg, since it starts at the photo's center).
// pointermove/up on document (not just the canvas) so a fast drag that
// briefly leaves the canvas bounds doesn't get dropped.
eggVisionOverlay.addEventListener("pointerdown", (e) => {
  if (!eggVisionState) return;
  const rect = eggVisionOverlay.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const scale = eggVisionDisplayScale();
  const coin = eggVisionState.coin;
  const coinX = coin.cx * scale;
  const coinY = coin.cy * scale;
  const coinR = coin.r * scale;
  const distFromCenter = Math.hypot(x - coinX, y - coinY);

  if (Math.abs(distFromCenter - coinR) < 14) {
    eggVisionDrag = { kind: "coin", mode: "resize" };
    return;
  }
  if (distFromCenter < coinR + 14) {
    eggVisionDrag = { kind: "coin", mode: "move" };
    return;
  }

  const eggIndex = eggVisionState.eggs.findIndex(
    (egg) => Math.hypot(x - egg.cx * scale, y - egg.cy * scale) < 20
  );
  if (eggIndex !== -1) {
    eggVisionDrag = { kind: "egg", index: eggIndex, mode: "move" };
  }
});

document.addEventListener("pointermove", (e) => {
  if (!eggVisionDrag || !eggVisionState) return;
  const rect = eggVisionOverlay.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const y = e.clientY - rect.top;
  const scale = eggVisionDisplayScale();

  if (eggVisionDrag.kind === "coin") {
    const coin = eggVisionState.coin;
    if (eggVisionDrag.mode === "move") {
      coin.cx = x / scale;
      coin.cy = y / scale;
    } else if (eggVisionDrag.mode === "resize") {
      coin.r = Math.max(4, Math.hypot(x - coin.cx * scale, y - coin.cy * scale) / scale);
    }
    recomputeEggSizes();
  } else if (eggVisionDrag.kind === "egg") {
    const egg = eggVisionState.eggs[eggVisionDrag.index];
    if (egg) {
      egg.cx = x / scale;
      egg.cy = y / scale;
    }
  }
  drawEggVisionOverlay();
  renderEggVisionChips();
});

document.addEventListener("pointerup", () => {
  eggVisionDrag = null;
});

function closeEggVisionReview() {
  eggVisionBackdrop.classList.remove("open");
  eggVisionState = null;
  eggVisionDrag = null;
}

document.getElementById("egg-vision-cancel-btn").addEventListener("click", closeEggVisionReview);
eggVisionBackdrop.addEventListener("click", (e) => {
  if (e.target === eggVisionBackdrop) closeEggVisionReview();
});

eggVisionUseBtn.addEventListener("click", () => {
  if (!eggVisionState) return;
  document.getElementById("count-value").textContent = eggVisionState.eggs.length;
  sheetForm.dataset.eggSizes = eggVisionState.eggs.map((e) => e.size).join(",");
  closeEggVisionReview();
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
  const width = totalCount * pointSpacing;
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

  let divider = "";
  if (forecastMonths.length > 0) {
    const dividerX = historyCount * pointSpacing;
    divider = `<line x1="${dividerX}" y1="${topPad}" x2="${dividerX}" y2="${topPad + chartH}" stroke="var(--border)" stroke-width="1" stroke-dasharray="3,3"></line>`;
  }

  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet">${content}${divider}</svg>`;
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
  const width = totalCount * pointSpacing;
  const height = topPad + chartH + labelH;
  const maxVal = Math.max(1, ...data.collected, ...data.advanced_ci_upper);

  const xAt = (i) => i * pointSpacing + pointSpacing / 2;
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

  let content = "";
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
}

tabButtons.forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.page));
});

trendsRangeSelect.addEventListener("change", loadTrends);

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

  const item = e.target.closest(".chicken-item");
  if (item) {
    const chicken = chickenCache[item.dataset.id];
    if (chicken) openChickenForm(chicken);
  }
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
