"use strict";

// --- Small helpers ---------------------------------------------------------

function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw new Error((body && body.error) || `server returned ${res.status}`);
  return body;
}

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

function openSheet(id) { document.getElementById(id).classList.add("open"); }
function closeSheet(id) { document.getElementById(id).classList.remove("open"); }

// Close a sheet when tapping its backdrop.
document.querySelectorAll(".sheet-backdrop").forEach((bd) => {
  bd.addEventListener("click", (e) => { if (e.target === bd) bd.classList.remove("open"); });
});

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDate(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}
function todayISO() { return new Date().toISOString().slice(0, 10); }

// --- Home: goal card -------------------------------------------------------

async function loadHome() {
  let data;
  try {
    data = await fetchJSON("api/weight");
  } catch (e) {
    return;
  }
  const goal = data.goal || {};

  const cw = data.current_weight_kg;
  document.getElementById("weight-current").textContent = cw != null ? `${cw} kg` : "–";
  document.getElementById("weight-target").textContent =
    goal.target_weight_kg != null ? `/ ${goal.target_weight_kg} kg` : "";
  setBar("weight-bar", data.weight_progress_pct);

  const bf = data.current_body_fat_pct;
  document.getElementById("bf-current").textContent = bf != null ? `${bf} %` : "— %";
  document.getElementById("bf-target").textContent =
    goal.target_body_fat_pct != null ? `/ ${goal.target_body_fat_pct} %` : "";
  setBar("bf-bar", data.body_fat_progress_pct);

  document.getElementById("lean-mass").textContent =
    data.lean_mass_kg != null ? `${data.lean_mass_kg} kg` : "—";
  document.getElementById("weight-remaining").textContent =
    data.weight_to_target_kg != null ? `${data.weight_to_target_kg > 0 ? "+" : ""}${data.weight_to_target_kg} kg` : "—";

  const days = data.days_remaining;
  document.getElementById("goal-days").textContent =
    days == null ? "—" : days >= 0 ? `${days} days left` : `${-days} days over`;

  document.getElementById("chart-target-note").textContent =
    goal.target_weight_kg != null ? `· target ${goal.target_weight_kg} kg` : "";
  renderForecast(data.forecast, goal);
  renderWeightChart(data.logs || [], goal, data.forecast);
}

const FORECAST_STATUS = {
  ahead: { cls: "good", badge: "Ahead" },
  on_track: { cls: "good", badge: "On track" },
  behind: { cls: "warn", badge: "Behind" },
  off_track: { cls: "bad", badge: "Off track" },
};

function renderForecast(forecast, goal) {
  const line = document.getElementById("forecast-line");
  if (!forecast || !forecast.available) {
    // Explain what's needed rather than showing nothing.
    line.hidden = false;
    document.getElementById("forecast-badge").textContent = "Forecast";
    document.getElementById("forecast-badge").className = "forecast-badge muted-badge";
    document.getElementById("forecast-text").textContent = "Log at least two weigh-ins to project your trend.";
    return;
  }
  const meta = FORECAST_STATUS[forecast.status] || FORECAST_STATUS.on_track;
  const badge = document.getElementById("forecast-badge");
  badge.textContent = meta.badge;
  badge.className = `forecast-badge forecast-${meta.cls}`;

  const rate = forecast.slope_per_week;
  const rateStr = `${rate > 0 ? "+" : ""}${rate} kg/wk`;
  const parts = [`Trending ${rateStr}, projected ${forecast.projected_weight_kg} kg by target.`];
  if (forecast.status === "behind" && forecast.required_per_week != null) {
    parts.push(`Need ${forecast.required_per_week > 0 ? "+" : ""}${forecast.required_per_week} kg/wk to reach ${goal.target_weight_kg}.`);
  } else if (forecast.status === "off_track" && forecast.required_per_week != null) {
    parts.push(`You're moving the wrong way — need ${forecast.required_per_week > 0 ? "+" : ""}${forecast.required_per_week} kg/wk.`);
  } else if ((forecast.status === "ahead" || forecast.status === "on_track") && forecast.projected_date) {
    parts.push(`On this trend you hit ${goal.target_weight_kg} kg around ${fmtDate(forecast.projected_date)}.`);
  }
  document.getElementById("forecast-text").textContent = parts.join(" ");
  line.hidden = false;
}

function setBar(id, pct) {
  document.getElementById(id).style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
}

// --- Weight chart (single-series line + target reference line) -------------

function renderWeightChart(logs, goal, forecast) {
  const host = document.getElementById("weight-chart");
  host.innerHTML = "";
  const points = logs
    .map((l) => ({ t: new Date(l.ts).getTime(), y: l.weight_kg }))
    .filter((p) => !isNaN(p.t))
    .sort((a, b) => a.t - b.t);

  if (!points.length) {
    host.innerHTML = '<p class="empty-state">Log a weight to see your trend.</p>';
    return;
  }

  const W = 320, H = 170, padL = 34, padR = 12, padT = 12, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const target = goal.target_weight_kg;

  let tMin = points[0].t;
  let tMax = points[points.length - 1].t;
  if (goal.target_date) {
    const td = new Date(goal.target_date).getTime();
    if (!isNaN(td)) tMax = Math.max(tMax, td);
  }
  if (tMax === tMin) tMax = tMin + 86400000;

  const ys = points.map((p) => p.y).concat(target != null ? [target] : []);
  let yMin = Math.min(...ys), yMax = Math.max(...ys);
  const pad = Math.max(0.5, (yMax - yMin) * 0.15);
  yMin -= pad; yMax += pad;

  const sx = (t) => padL + ((t - tMin) / (tMax - tMin)) * plotW;
  const sy = (y) => padT + (1 - (y - yMin) / (yMax - yMin)) * plotH;

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Weight over time");

  // Horizontal gridlines + y labels
  const ticks = 3;
  for (let i = 0; i <= ticks; i++) {
    const yVal = yMin + (i / ticks) * (yMax - yMin);
    const y = sy(yVal);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", padL); line.setAttribute("x2", W - padR);
    line.setAttribute("y1", y); line.setAttribute("y2", y);
    line.setAttribute("class", "chart-grid-line");
    svg.appendChild(line);
    const lbl = document.createElementNS(NS, "text");
    lbl.setAttribute("x", padL - 5); lbl.setAttribute("y", y + 3);
    lbl.setAttribute("text-anchor", "end");
    lbl.setAttribute("class", "chart-axis-label");
    lbl.textContent = yVal.toFixed(0);
    svg.appendChild(lbl);
  }

  // Target reference line
  if (target != null) {
    const y = sy(target);
    const tl = document.createElementNS(NS, "line");
    tl.setAttribute("x1", padL); tl.setAttribute("x2", W - padR);
    tl.setAttribute("y1", y); tl.setAttribute("y2", y);
    tl.setAttribute("class", "chart-target");
    svg.appendChild(tl);
    const tlbl = document.createElementNS(NS, "text");
    tlbl.setAttribute("x", W - padR); tlbl.setAttribute("y", y - 4);
    tlbl.setAttribute("text-anchor", "end");
    tlbl.setAttribute("class", "chart-target-label");
    tlbl.textContent = `Target ${target}`;
    svg.appendChild(tlbl);
  }

  // Projected trend line (dashed) — drawn under the actual line. It may run
  // past the plot vertically; the SVG clips it, which reads correctly as
  // "trending off the top/bottom".
  if (forecast && forecast.available && forecast.trend && forecast.trend.length === 2) {
    const a = forecast.trend[0], b = forecast.trend[1];
    const x1 = sx(new Date(a.ts).getTime()), y1 = sy(a.weight_kg);
    const x2 = sx(new Date(b.ts).getTime()), y2 = sy(b.weight_kg);
    const tr = document.createElementNS(NS, "line");
    tr.setAttribute("x1", x1); tr.setAttribute("y1", y1);
    tr.setAttribute("x2", x2); tr.setAttribute("y2", y2);
    tr.setAttribute("class", "chart-trend");
    svg.appendChild(tr);
    // Label at the line's midpoint (kept inside the plot), so it never
    // collides with the "Target" label pinned to the top-right.
    const mx = (Math.max(padL, Math.min(W - padR, x1)) + Math.max(padL, Math.min(W - padR, x2))) / 2;
    const my = Math.max(padT + 10, Math.min(padT + plotH - 4, (y1 + y2) / 2 - 5));
    const plbl = document.createElementNS(NS, "text");
    plbl.setAttribute("x", mx);
    plbl.setAttribute("y", my);
    plbl.setAttribute("text-anchor", "middle");
    plbl.setAttribute("class", "chart-trend-label");
    plbl.textContent = "Projected";
    svg.appendChild(plbl);
  }

  // Weight line
  if (points.length > 1) {
    const path = document.createElementNS(NS, "polyline");
    path.setAttribute("points", points.map((p) => `${sx(p.t)},${sy(p.y)}`).join(" "));
    path.setAttribute("class", "chart-line");
    svg.appendChild(path);
  }
  // Markers
  points.forEach((p) => {
    const c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", sx(p.t)); c.setAttribute("cy", sy(p.y)); c.setAttribute("r", 3.5);
    c.setAttribute("class", "chart-marker");
    svg.appendChild(c);
  });

  // x labels: first + last
  const xlbl = (t, anchor, x) => {
    const el = document.createElementNS(NS, "text");
    el.setAttribute("x", x); el.setAttribute("y", H - 6);
    el.setAttribute("text-anchor", anchor);
    el.setAttribute("class", "chart-axis-label");
    el.textContent = fmtDate(new Date(t).toISOString());
    svg.appendChild(el);
  };
  xlbl(tMin, "start", padL);
  xlbl(tMax, "end", W - padR);

  // Hover crosshair + tooltip
  const cross = document.createElementNS(NS, "line");
  cross.setAttribute("class", "chart-crosshair");
  cross.setAttribute("y1", padT); cross.setAttribute("y2", padT + plotH);
  cross.style.opacity = "0";
  svg.appendChild(cross);
  const hit = document.createElementNS(NS, "rect");
  hit.setAttribute("x", padL); hit.setAttribute("y", padT);
  hit.setAttribute("width", plotW); hit.setAttribute("height", plotH);
  hit.setAttribute("class", "chart-hit");
  svg.appendChild(hit);

  host.style.position = "relative";
  const tip = document.createElement("div");
  tip.className = "chart-tooltip";
  host.appendChild(tip);

  function onMove(evt) {
    const rect = svg.getBoundingClientRect();
    const clientX = (evt.touches ? evt.touches[0].clientX : evt.clientX);
    const vx = ((clientX - rect.left) / rect.width) * W;
    let nearest = points[0], best = Infinity;
    for (const p of points) {
      const d = Math.abs(sx(p.t) - vx);
      if (d < best) { best = d; nearest = p; }
    }
    const px = sx(nearest.t), py = sy(nearest.y);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px);
    cross.style.opacity = "1";
    tip.style.left = `${(px / W) * 100}%`;
    tip.style.top = `${(py / H) * 100}%`;
    tip.innerHTML = `<strong>${nearest.y} kg</strong><br>${escapeHtml(fmtDate(new Date(nearest.t).toISOString()))}`;
    tip.style.opacity = "1";
  }
  function onLeave() { cross.style.opacity = "0"; tip.style.opacity = "0"; }
  hit.addEventListener("mousemove", onMove);
  hit.addEventListener("mouseleave", onLeave);
  hit.addEventListener("touchstart", onMove, { passive: true });
  hit.addEventListener("touchmove", onMove, { passive: true });
  hit.addEventListener("touchend", onLeave);

  host.appendChild(svg);
}

// --- Daily challenge -------------------------------------------------------

async function loadChallenge() {
  let data;
  try { data = await fetchJSON("api/challenge"); } catch (e) { return; }
  document.getElementById("challenge-streak").textContent = `🔥 ${data.streak}`;

  const list = document.getElementById("challenge-list");
  list.innerHTML = (data.items || [])
    .map(
      (it) => `
      <li class="challenge-item ${it.done_today ? "done" : ""}" data-id="${it.id}">
        <span class="challenge-check">${it.done_today ? "✓" : ""}</span>
        <span class="challenge-label">${escapeHtml(it.label)}</span>
      </li>`
    )
    .join("");

  const week = document.getElementById("challenge-week");
  week.innerHTML = (data.last_7_days || [])
    .map(
      (d) => `<span class="week-dot ${d.complete ? "on" : ""} ${d.day === data.today ? "today" : ""}" title="${d.day}"></span>`
    )
    .join("");
}

document.getElementById("challenge-list").addEventListener("click", async (e) => {
  const item = e.target.closest(".challenge-item");
  if (!item) return;
  try {
    await fetchJSON("api/challenge/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: Number(item.dataset.id) }),
    });
    loadChallenge();
  } catch (err) {
    toast("Couldn't update — check your connection.");
  }
});

// --- Challenge items management (typed: exercise / supplement) --------------

async function loadChallengeItems() {
  const list = document.getElementById("challenge-items-list");
  let items;
  try { items = await fetchJSON("api/challenge/items"); } catch (e) { return; }
  list.innerHTML = items
    .map((it) => {
      const icon = it.item_type === "supplement" ? "💊" : "🏋️";
      // Editable target (exercise) or dose (supplement), inline.
      const editField =
        it.item_type === "supplement"
          ? `<input type="text" class="ci-edit-dose" data-id="${it.id}" value="${escapeHtml(it.dose || "")}" placeholder="dose">`
          : `<input type="number" class="ci-edit-reps" data-id="${it.id}" value="${it.target_reps != null ? it.target_reps : ""}" placeholder="reps" min="0">`;
      return `
        <li data-id="${it.id}">
          <span class="ci-icon">${icon}</span>
          <span class="ci-name">${escapeHtml(it.name)}</span>
          ${editField}
          <button type="button" class="list-del ci-del" data-id="${it.id}" aria-label="Remove">✕</button>
        </li>`;
    })
    .join("");
}

async function populateChallengeItemForm() {
  // Fill the exercise + supplement dropdowns from the libraries.
  let groups = [], sups = [];
  try { groups = await fetchJSON("api/exercises"); } catch (e) { /* ignore */ }
  try { sups = await fetchJSON("api/supplements"); } catch (e) { /* ignore */ }
  document.getElementById("ci-exercise").innerHTML = groups
    .map((g) => `<optgroup label="${escapeHtml(g.equipment)}">${g.exercises
      .map((ex) => `<option value="${ex.id}">${escapeHtml(ex.name)}</option>`).join("")}</optgroup>`)
    .join("");
  document.getElementById("ci-supplement").innerHTML = sups
    .map((s) => `<option value="${s.id}">${escapeHtml(s.name)}${s.dose ? " (" + escapeHtml(s.dose) + ")" : ""}</option>`)
    .join("");
}

function syncChallengeItemFields() {
  const isSupp = document.getElementById("ci-type").value === "supplement";
  document.getElementById("ci-exercise-fields").hidden = isSupp;
  document.getElementById("ci-supplement-fields").hidden = !isSupp;
}
document.getElementById("ci-type").addEventListener("change", syncChallengeItemFields);

document.getElementById("challenge-manage-btn").addEventListener("click", async () => {
  openSheet("challenge-items-backdrop");
  await populateChallengeItemForm();
  syncChallengeItemFields();
  loadChallengeItems();
});
document.getElementById("challenge-items-close-btn").addEventListener("click", () => {
  closeSheet("challenge-items-backdrop");
  loadChallenge();
});

document.getElementById("challenge-item-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const type = document.getElementById("ci-type").value;
  let payload;
  if (type === "supplement") {
    const supplement_id = document.getElementById("ci-supplement").value;
    if (!supplement_id) { toast("Add a supplement in the Library first."); return; }
    payload = { item_type: "supplement", supplement_id, dose: document.getElementById("ci-dose").value };
  } else {
    const exercise_id = document.getElementById("ci-exercise").value;
    if (!exercise_id) { toast("Add an exercise in the Library first."); return; }
    payload = {
      item_type: "exercise",
      exercise_id,
      target_sets: document.getElementById("ci-sets").value,
      target_reps: document.getElementById("ci-reps").value,
    };
  }
  try {
    await fetchJSON("api/challenge/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    document.getElementById("ci-dose").value = "";
    document.getElementById("ci-sets").value = "";
    document.getElementById("ci-reps").value = "";
    loadChallengeItems();
  } catch (err) { toast(err.message); }
});

document.getElementById("challenge-items-list").addEventListener("click", async (e) => {
  const del = e.target.closest(".ci-del");
  if (!del) return;
  if (!confirm("Remove this challenge item? Past streaks are kept.")) return;
  try {
    await fetchJSON(`api/challenge/items/${del.dataset.id}`, { method: "DELETE" });
    loadChallengeItems();
  } catch (err) { toast(err.message); }
});
document.getElementById("challenge-items-list").addEventListener("change", async (e) => {
  const reps = e.target.closest(".ci-edit-reps");
  const dose = e.target.closest(".ci-edit-dose");
  const field = reps || dose;
  if (!field) return;
  const payload = reps ? { target_reps: field.value } : { dose: field.value };
  try {
    await fetchJSON(`api/challenge/items/${field.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) { toast(err.message); }
});

// --- Challenge history (edit past days) ------------------------------------

document.getElementById("challenge-history-btn").addEventListener("click", () => {
  openSheet("challenge-history-backdrop");
  // Default to the last two weeks; the user widens "From" to backfill older.
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 13);
  document.getElementById("history-to").value = to.toISOString().slice(0, 10);
  document.getElementById("history-from").value = from.toISOString().slice(0, 10);
  loadChallengeHistory();
});
document.getElementById("challenge-history-close-btn").addEventListener("click", () => {
  closeSheet("challenge-history-backdrop");
  loadChallenge();
});
document.getElementById("history-from").addEventListener("change", loadChallengeHistory);
document.getElementById("history-to").addEventListener("change", loadChallengeHistory);

async function loadChallengeHistory() {
  const host = document.getElementById("history-grid");
  const from = document.getElementById("history-from").value;
  const to = document.getElementById("history-to").value;
  const qs = from && to ? `from=${from}&to=${to}` : "days=14";
  let data;
  try { data = await fetchJSON(`api/challenge/history?${qs}`); } catch (e) { return; }
  const items = data.items || [];
  host.innerHTML = data.days
    .map((d) => {
      const done = new Set(d.done);
      const cells = items
        .map(
          (it) => `<button type="button" class="history-cell ${done.has(it.id) ? "on" : ""}"
            data-item="${it.id}" data-day="${d.day}" title="${escapeHtml(it.name)}">${it.item_type === "supplement" ? "💊" : "🏋️"}</button>`
        )
        .join("");
      return `
        <div class="history-row ${d.complete ? "complete" : ""}">
          <span class="history-day">${escapeHtml(fmtDate(d.day))}</span>
          <div class="history-cells">${cells}</div>
        </div>`;
    })
    .join("");
}

document.getElementById("history-grid").addEventListener("click", async (e) => {
  const cell = e.target.closest(".history-cell");
  if (!cell) return;
  try {
    await fetchJSON("api/challenge/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item_id: Number(cell.dataset.item), day: cell.dataset.day }),
    });
    loadChallengeHistory();
  } catch (err) { toast(err.message); }
});

// --- Weight sheet ----------------------------------------------------------

document.getElementById("log-weight-btn").addEventListener("click", () => {
  openSheet("weight-backdrop");
  resetWeightForm();
  loadWeightHistory();
});
document.getElementById("weight-close-btn").addEventListener("click", () => {
  closeSheet("weight-backdrop");
  loadHome();
});

function resetWeightForm() {
  document.getElementById("weight-form-id").value = "";
  document.getElementById("weight-form-kg").value = "";
  document.getElementById("weight-form-bf").value = "";
  document.getElementById("weight-form-notes").value = "";
  document.getElementById("weight-form-date").value = todayISO();
  document.getElementById("weight-form-save").textContent = "Add";
  document.getElementById("weight-form-cancel").hidden = true;
}

document.getElementById("weight-form-cancel").addEventListener("click", resetWeightForm);

document.getElementById("weight-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("weight-form-id").value;
  const payload = {
    weight_kg: document.getElementById("weight-form-kg").value,
    body_fat_pct: document.getElementById("weight-form-bf").value,
    notes: document.getElementById("weight-form-notes").value,
    date: document.getElementById("weight-form-date").value,
  };
  try {
    if (id) {
      await fetchJSON(`api/weight/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      await fetchJSON("api/weight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    resetWeightForm();
    loadWeightHistory();
    toast("Weight saved.");
  } catch (err) { toast(err.message); }
});

async function loadWeightHistory() {
  const list = document.getElementById("weight-history");
  let data;
  try { data = await fetchJSON("api/weight"); } catch (e) { return; }
  const logs = (data.logs || []).slice().reverse();
  list.innerHTML = logs
    .map((l) => {
      const bf = l.body_fat_pct != null ? ` · ${l.body_fat_pct}% bf` : "";
      const note = l.notes ? ` · ${escapeHtml(l.notes)}` : "";
      return `
        <li data-id="${l.id}">
          <div class="list-main">
            <div class="list-title">${l.weight_kg} kg</div>
            <div class="list-sub">${escapeHtml(fmtDate(l.ts))}${bf}${note}</div>
          </div>
          <div>
            <button type="button" class="link-btn weight-edit" data-id="${l.id}">Edit</button>
            <button type="button" class="list-del weight-del" data-id="${l.id}" aria-label="Delete">✕</button>
          </div>
        </li>`;
    })
    .join("");
  window._weightLogs = data.logs || [];
}

document.getElementById("weight-history").addEventListener("click", async (e) => {
  const del = e.target.closest(".weight-del");
  if (del) {
    if (!confirm("Delete this weigh-in?")) return;
    try { await fetchJSON(`api/weight/${del.dataset.id}`, { method: "DELETE" }); loadWeightHistory(); }
    catch (err) { toast(err.message); }
    return;
  }
  const edit = e.target.closest(".weight-edit");
  if (edit) {
    const log = (window._weightLogs || []).find((l) => String(l.id) === edit.dataset.id);
    if (!log) return;
    document.getElementById("weight-form-id").value = log.id;
    document.getElementById("weight-form-kg").value = log.weight_kg;
    document.getElementById("weight-form-bf").value = log.body_fat_pct != null ? log.body_fat_pct : "";
    document.getElementById("weight-form-notes").value = log.notes || "";
    document.getElementById("weight-form-date").value = log.ts.slice(0, 10);
    document.getElementById("weight-form-save").textContent = "Save";
    document.getElementById("weight-form-cancel").hidden = false;
    document.getElementById("weight-form-kg").focus();
  }
});

// --- Library: exercises + supplements --------------------------------------

let exerciseGroups = [];

document.getElementById("library-open-btn").addEventListener("click", () => {
  openSheet("library-backdrop");
  loadExercises();
  loadSupplements();
});
document.getElementById("library-close-btn").addEventListener("click", () => closeSheet("library-backdrop"));

// Library tabs
document.querySelectorAll(".lib-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".lib-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const which = tab.dataset.tab;
    document.getElementById("lib-exercises").hidden = which !== "exercises";
    document.getElementById("lib-supplements").hidden = which !== "supplements";
  });
});

async function loadExercises() {
  try { exerciseGroups = await fetchJSON("api/exercises"); } catch (e) { return; }
  const host = document.getElementById("exercises-groups");
  host.innerHTML = exerciseGroups
    .map(
      (g) => `
      <div class="equip-group">
        <h3>${escapeHtml(g.equipment)}</h3>
        ${g.exercises
          .map(
            (ex) => `
          <div class="exercise-row" data-id="${ex.id}">
            <div class="ex-main">
              <div class="ex-name">${escapeHtml(ex.name)}</div>
              ${ex.category ? `<div class="ex-cat">${escapeHtml(ex.category)}</div>` : ""}
            </div>
            <div class="exercise-actions">
              <button type="button" class="link-btn ex-log-btn" data-id="${ex.id}">Log</button>
              <button type="button" class="link-btn ex-edit" data-id="${ex.id}" aria-label="Edit">✎</button>
              <button type="button" class="list-del ex-del" data-id="${ex.id}" aria-label="Remove">✕</button>
            </div>
          </div>`
          )
          .join("")}
      </div>`
    )
    .join("");

  const equipment = [...new Set(exerciseGroups.map((g) => g.equipment))];
  document.getElementById("equipment-list").innerHTML = equipment
    .map((eq) => `<option value="${escapeHtml(eq)}">`).join("");
}

document.getElementById("exercise-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("exercise-form-name").value.trim();
  const equipment = document.getElementById("exercise-form-equipment").value.trim() || "Bodyweight";
  if (!name) return;
  try {
    await fetchJSON("api/exercises", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, equipment }),
    });
    document.getElementById("exercise-form-name").value = "";
    document.getElementById("exercise-form-equipment").value = "";
    loadExercises();
  } catch (err) { toast(err.message); }
});

document.getElementById("exercises-groups").addEventListener("click", async (e) => {
  const del = e.target.closest(".ex-del");
  if (del) {
    if (!confirm("Remove this exercise? Logged history is kept.")) return;
    try { await fetchJSON(`api/exercises/${del.dataset.id}`, { method: "DELETE" }); loadExercises(); }
    catch (err) { toast(err.message); }
    return;
  }
  const edit = e.target.closest(".ex-edit");
  if (edit) {
    const ex = exerciseGroups.flatMap((g) => g.exercises).find((x) => String(x.id) === edit.dataset.id);
    if (!ex) return;
    const name = prompt("Exercise name:", ex.name);
    if (name == null) return;
    const equipment = prompt("Equipment:", ex.equipment);
    if (equipment == null) return;
    try {
      await fetchJSON(`api/exercises/${ex.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), equipment: equipment.trim() }),
      });
      loadExercises();
    } catch (err) { toast(err.message); }
    return;
  }
  const log = e.target.closest(".ex-log-btn") || e.target.closest(".exercise-row");
  if (log) openWorkoutSheet(Number(log.dataset.id));
});

// Supplements
let supplementsCache = [];

async function loadSupplements() {
  try { supplementsCache = await fetchJSON("api/supplements"); } catch (e) { return; }
  document.getElementById("supplements-list").innerHTML = supplementsCache.length
    ? supplementsCache
        .map(
          (s) => `
        <li data-id="${s.id}">
          <span class="ci-icon">💊</span>
          <span class="ci-name">${escapeHtml(s.name)}${s.dose ? ` <span class="muted">· ${escapeHtml(s.dose)}</span>` : ""}</span>
          <button type="button" class="link-btn sup-edit" data-id="${s.id}" aria-label="Edit">✎</button>
          <button type="button" class="list-del sup-del" data-id="${s.id}" aria-label="Remove">✕</button>
        </li>`
        )
        .join("")
    : '<li class="empty-state">No supplements yet.</li>';
}

document.getElementById("supplement-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("supplement-form-name").value.trim();
  const dose = document.getElementById("supplement-form-dose").value.trim();
  if (!name) return;
  try {
    await fetchJSON("api/supplements", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, dose }),
    });
    document.getElementById("supplement-form-name").value = "";
    document.getElementById("supplement-form-dose").value = "";
    loadSupplements();
  } catch (err) { toast(err.message); }
});

document.getElementById("supplements-list").addEventListener("click", async (e) => {
  const del = e.target.closest(".sup-del");
  if (del) {
    if (!confirm("Remove this supplement?")) return;
    try { await fetchJSON(`api/supplements/${del.dataset.id}`, { method: "DELETE" }); loadSupplements(); }
    catch (err) { toast(err.message); }
    return;
  }
  const edit = e.target.closest(".sup-edit");
  if (edit) {
    const s = supplementsCache.find((x) => String(x.id) === edit.dataset.id);
    if (!s) return;
    const name = prompt("Supplement name:", s.name);
    if (name == null) return;
    const dose = prompt("Default dose:", s.dose || "");
    if (dose == null) return;
    try {
      await fetchJSON(`api/supplements/${s.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), dose: dose.trim() }),
      });
      loadSupplements();
    } catch (err) { toast(err.message); }
  }
});

// Workout log sheet
let workoutFilterExerciseId = null;
let workoutHistoryCache = [];

document.getElementById("log-workout-btn").addEventListener("click", () => openWorkoutSheet(null));
document.getElementById("workout-form-cancel").addEventListener("click", () => {
  closeSheet("workout-backdrop");
  loadRecentWorkouts();
});

function resetWorkoutForm() {
  document.getElementById("workout-form-id").value = "";
  document.getElementById("workout-form-sets").value = "";
  document.getElementById("workout-form-reps").value = "";
  document.getElementById("workout-form-weight").value = "";
  document.getElementById("workout-form-duration").value = "";
  document.getElementById("workout-form-notes").value = "";
  document.getElementById("workout-form-date").value = todayISO();
  document.getElementById("workout-title").textContent = "Log a set";
  document.getElementById("workout-form-save").textContent = "Save set";
}

async function openWorkoutSheet(exerciseId) {
  if (!exerciseGroups.length) {
    try { exerciseGroups = await fetchJSON("api/exercises"); } catch (e) { /* ignore */ }
  }
  const select = document.getElementById("workout-form-exercise");
  select.innerHTML = exerciseGroups
    .map(
      (g) => `<optgroup label="${escapeHtml(g.equipment)}">${g.exercises
        .map((ex) => `<option value="${ex.id}">${escapeHtml(ex.name)}</option>`)
        .join("")}</optgroup>`
    )
    .join("");
  resetWorkoutForm();
  if (exerciseId) select.value = String(exerciseId);
  workoutFilterExerciseId = exerciseId || null;
  openSheet("workout-backdrop");
  loadWorkoutHistory(exerciseId);
}

document.getElementById("workout-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("workout-form-id").value;
  const payload = {
    exercise_id: document.getElementById("workout-form-exercise").value,
    sets: document.getElementById("workout-form-sets").value,
    reps: document.getElementById("workout-form-reps").value,
    weight_kg: document.getElementById("workout-form-weight").value,
    duration_sec: document.getElementById("workout-form-duration").value,
    notes: document.getElementById("workout-form-notes").value,
    date: document.getElementById("workout-form-date").value,
  };
  try {
    if (id) {
      await fetchJSON(`api/workouts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Workout updated.");
    } else {
      await fetchJSON("api/workouts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Set logged.");
    }
    resetWorkoutForm();
    loadWorkoutHistory(workoutFilterExerciseId);
    loadRecentWorkouts();
  } catch (err) { toast(err.message); }
});

function workoutSummary(w) {
  const bits = [];
  if (w.sets != null && w.reps != null) bits.push(`${w.sets}×${w.reps}`);
  else if (w.reps != null) bits.push(`${w.reps} reps`);
  else if (w.sets != null) bits.push(`${w.sets} sets`);
  if (w.weight_kg != null) bits.push(`@ ${w.weight_kg} kg`);
  if (w.duration_sec != null) bits.push(`${w.duration_sec}s`);
  return bits.join(" ");
}

async function loadWorkoutHistory(exerciseId) {
  const list = document.getElementById("workout-history");
  let rows;
  try {
    rows = await fetchJSON(exerciseId ? `api/workouts?exercise_id=${exerciseId}` : "api/workouts");
  } catch (e) { return; }
  workoutHistoryCache = rows;
  list.innerHTML = rows.length
    ? rows
        .map((w) => {
          // Challenge-logged sets are managed by the challenge check-off, so
          // they're marked and not directly editable/deletable here.
          const fromChallenge = w.source === "challenge";
          const actions = fromChallenge
            ? '<span class="tag muted">from challenge</span>'
            : `<button type="button" class="link-btn workout-edit" data-id="${w.id}">Edit</button>
               <button type="button" class="list-del workout-del" data-id="${w.id}" aria-label="Delete">✕</button>`;
          return `
      <li data-id="${w.id}">
        <div class="list-main">
          <div class="list-title">${escapeHtml(w.exercise_name)}</div>
          <div class="list-sub">${escapeHtml(fmtDate(w.ts))}${workoutSummary(w) ? " · " + escapeHtml(workoutSummary(w)) : ""}</div>
        </div>
        <div class="row-actions">${actions}</div>
      </li>`;
        })
        .join("")
    : '<li class="empty-state">Nothing logged yet.</li>';
}

document.getElementById("workout-history").addEventListener("click", async (e) => {
  const del = e.target.closest(".workout-del");
  if (del) {
    try {
      await fetchJSON(`api/workouts/${del.dataset.id}`, { method: "DELETE" });
      loadWorkoutHistory(workoutFilterExerciseId);
      loadRecentWorkouts();
    } catch (err) { toast(err.message); }
    return;
  }
  const edit = e.target.closest(".workout-edit");
  if (edit) {
    const w = workoutHistoryCache.find((x) => String(x.id) === edit.dataset.id);
    if (!w) return;
    document.getElementById("workout-form-id").value = w.id;
    document.getElementById("workout-form-exercise").value = String(w.exercise_id);
    document.getElementById("workout-form-sets").value = w.sets != null ? w.sets : "";
    document.getElementById("workout-form-reps").value = w.reps != null ? w.reps : "";
    document.getElementById("workout-form-weight").value = w.weight_kg != null ? w.weight_kg : "";
    document.getElementById("workout-form-duration").value = w.duration_sec != null ? w.duration_sec : "";
    document.getElementById("workout-form-notes").value = w.notes || "";
    document.getElementById("workout-form-date").value = w.ts.slice(0, 10);
    document.getElementById("workout-title").textContent = "Edit workout";
    document.getElementById("workout-form-save").textContent = "Update";
    document.getElementById("workout-backdrop").querySelector(".sheet").scrollTop = 0;
  }
});

async function loadRecentWorkouts() {
  const list = document.getElementById("recent-workout-list");
  const empty = document.getElementById("recent-workout-empty");
  let rows;
  try { rows = await fetchJSON("api/workouts"); } catch (e) { return; }
  rows = rows.slice(0, 5);
  empty.hidden = rows.length > 0;
  list.innerHTML = rows
    .map(
      (w) => `
      <li>
        <div class="list-main">
          <div class="list-title">${escapeHtml(w.exercise_name)}</div>
          <div class="list-sub">${escapeHtml(fmtDate(w.ts))}${workoutSummary(w) ? " · " + escapeHtml(workoutSummary(w)) : ""}</div>
        </div>
      </li>`
    )
    .join("");
}

// --- Reminders -------------------------------------------------------------

document.getElementById("reminders-open-btn").addEventListener("click", () => {
  openSheet("reminders-backdrop");
  loadReminders();
});
document.getElementById("reminders-close-btn").addEventListener("click", () => closeSheet("reminders-backdrop"));

async function loadReminders() {
  let data;
  try { data = await fetchJSON("api/reminders"); } catch (e) { return; }
  const badge = (on) => `<span class="badge ${on ? "badge-on" : "badge-off"}">${on ? "On" : "Off"}</span>`;
  document.getElementById("reminder-status").innerHTML = `
    <div class="reminder-line">${badge(data.challenge.enabled)}<span>Daily challenge · ${escapeHtml(data.challenge.time)}</span></div>
    <div class="reminder-line">${badge(data.weighin.enabled)}<span>Weekly weigh-in · ${escapeHtml(data.weighin.weekday)} ${escapeHtml(data.weighin.time)}</span></div>`;

  const select = document.getElementById("notify-service-select");
  let svc;
  try { svc = await fetchJSON("api/notify-services"); } catch (e) { svc = { services: [] }; }
  const services = svc.services || [];
  const current = data.notify_service;
  const opts = ['<option value="">(none configured)</option>']
    .concat(services.map((s) => `<option value="${escapeHtml(s)}" ${s === current ? "selected" : ""}>${escapeHtml(s)}</option>`));
  if (current && !services.includes(current)) opts.push(`<option value="${escapeHtml(current)}" selected>${escapeHtml(current)} (current)</option>`);
  select.innerHTML = opts.join("");

  document.getElementById("notify-hint").textContent = current
    ? `Current notify service: ${current}. Change it on the add-on's Configuration tab; this list is for reference and testing.`
    : "No notify service set. Add one as notify_service on the add-on's Configuration tab to enable reminders.";
}

document.getElementById("notify-test-btn").addEventListener("click", async () => {
  const result = document.getElementById("notify-test-result");
  result.textContent = "Sending…";
  try {
    await fetchJSON("api/notify-test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    result.textContent = "Test notification sent.";
  } catch (err) {
    result.textContent = `Couldn't send: ${err.message}`;
  }
});

// --- Settings --------------------------------------------------------------

document.getElementById("settings-open-btn").addEventListener("click", () => {
  openSheet("settings-backdrop");
  loadSettings();
});
document.getElementById("settings-close-btn").addEventListener("click", () => {
  closeSheet("settings-backdrop");
  loadHome();
});

async function loadSettings() {
  let goal;
  try { goal = await fetchJSON("api/goal"); } catch (e) { goal = window.GYM.goal || {}; }
  document.getElementById("goal-form-weight").value = goal.target_weight_kg != null ? goal.target_weight_kg : "";
  document.getElementById("goal-form-bf").value = goal.target_body_fat_pct != null ? goal.target_body_fat_pct : "";
  document.getElementById("goal-form-date").value = goal.target_date || "";
  document.getElementById("goal-form-start-weight").value = goal.start_weight_kg != null ? goal.start_weight_kg : "";

  try {
    const dbg = await fetchJSON("api/debug");
    document.getElementById("user-id").textContent = dbg.ingress_user_id || "(unknown — open through Home Assistant)";
  } catch (e) { /* ignore */ }
}

document.getElementById("goal-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = document.getElementById("goal-form-result");
  const payload = {
    target_weight_kg: document.getElementById("goal-form-weight").value,
    target_body_fat_pct: document.getElementById("goal-form-bf").value,
    target_date: document.getElementById("goal-form-date").value,
    start_weight_kg: document.getElementById("goal-form-start-weight").value,
  };
  try {
    await fetchJSON("api/goal", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    result.textContent = "Goal saved.";
  } catch (err) { result.textContent = err.message; }
});

document.getElementById("restore-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  const result = document.getElementById("restore-result");
  if (!file) return;
  if (!confirm("Restore this backup? It replaces all current data.")) { e.target.value = ""; return; }
  result.textContent = "Restoring…";
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch("api/restore", { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `server returned ${res.status}`);
    result.textContent = "Restored. Reloading…";
    setTimeout(() => location.reload(), 900);
  } catch (err) {
    result.textContent = `Restore failed: ${err.message}`;
  }
  e.target.value = "";
});

// --- HA status dot ---------------------------------------------------------

async function pingStatus() {
  const dot = document.getElementById("ha-status-dot");
  try {
    const dbg = await fetchJSON("api/debug");
    dot.className = "ha-status-dot " + (dbg.ha_api_reachable ? "status-ok" : "status-error");
    dot.title = dbg.ha_api_reachable ? "Home Assistant connected" : (dbg.ha_api_error || "Home Assistant not reachable");
  } catch (e) {
    dot.className = "ha-status-dot status-error";
  }
}

// --- Boot ------------------------------------------------------------------

loadHome();
loadChallenge();
loadRecentWorkouts();
pingStatus();
