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
  renderWeightChart(data.logs || [], goal);
}

function setBar(id, pct) {
  document.getElementById(id).style.width = `${Math.max(0, Math.min(100, pct || 0))}%`;
}

// --- Weight chart (single-series line + target reference line) -------------

function renderWeightChart(logs, goal) {
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

// --- Challenge items management --------------------------------------------

async function loadChallengeItems() {
  const list = document.getElementById("challenge-items-list");
  let items;
  try { items = await fetchJSON("api/challenge/items"); } catch (e) { return; }
  list.innerHTML = items
    .map(
      (it) => `
      <li data-id="${it.id}">
        <input type="text" value="${escapeHtml(it.label)}" data-id="${it.id}" class="item-edit">
        <button type="button" class="list-del item-del" data-id="${it.id}" aria-label="Remove">✕</button>
      </li>`
    )
    .join("");
}

document.getElementById("challenge-manage-btn").addEventListener("click", () => {
  openSheet("challenge-items-backdrop");
  loadChallengeItems();
});
document.getElementById("challenge-items-close-btn").addEventListener("click", () => {
  closeSheet("challenge-items-backdrop");
  loadChallenge();
});
document.getElementById("challenge-item-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("challenge-item-input");
  const label = input.value.trim();
  if (!label) return;
  try {
    await fetchJSON("api/challenge/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    input.value = "";
    loadChallengeItems();
  } catch (err) { toast(err.message); }
});
document.getElementById("challenge-items-list").addEventListener("click", async (e) => {
  const del = e.target.closest(".item-del");
  if (!del) return;
  if (!confirm("Remove this challenge item? Past streaks are kept.")) return;
  try {
    await fetchJSON(`api/challenge/items/${del.dataset.id}`, { method: "DELETE" });
    loadChallengeItems();
  } catch (err) { toast(err.message); }
});
document.getElementById("challenge-items-list").addEventListener("change", async (e) => {
  const input = e.target.closest(".item-edit");
  if (!input) return;
  const label = input.value.trim();
  if (!label) return;
  try {
    await fetchJSON(`api/challenge/items/${input.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
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

// --- Exercises + workouts --------------------------------------------------

let exerciseGroups = [];

document.getElementById("exercises-open-btn").addEventListener("click", () => {
  openSheet("exercises-backdrop");
  loadExercises();
});
document.getElementById("exercises-close-btn").addEventListener("click", () => closeSheet("exercises-backdrop"));

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
            <div>
              <div class="ex-name">${escapeHtml(ex.name)}</div>
              ${ex.category ? `<div class="ex-cat">${escapeHtml(ex.category)}</div>` : ""}
            </div>
            <div class="exercise-actions">
              <button type="button" class="link-btn ex-log-btn" data-id="${ex.id}">Log</button>
              <button type="button" class="list-del ex-del" data-id="${ex.id}" aria-label="Remove">✕</button>
            </div>
          </div>`
          )
          .join("")}
      </div>`
    )
    .join("");

  // Equipment datalist for the add form + workout exercise select.
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
  const log = e.target.closest(".ex-log-btn") || e.target.closest(".exercise-row");
  if (log) openWorkoutSheet(Number(log.dataset.id));
});

// Workout log sheet
document.getElementById("log-workout-btn").addEventListener("click", () => openWorkoutSheet(null));
document.getElementById("workout-form-cancel").addEventListener("click", () => {
  closeSheet("workout-backdrop");
  loadRecentWorkouts();
});

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
  if (exerciseId) select.value = String(exerciseId);
  document.getElementById("workout-form-sets").value = "";
  document.getElementById("workout-form-reps").value = "";
  document.getElementById("workout-form-weight").value = "";
  document.getElementById("workout-form-duration").value = "";
  document.getElementById("workout-form-notes").value = "";
  openSheet("workout-backdrop");
  loadWorkoutHistory(exerciseId);
}

document.getElementById("workout-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    exercise_id: document.getElementById("workout-form-exercise").value,
    sets: document.getElementById("workout-form-sets").value,
    reps: document.getElementById("workout-form-reps").value,
    weight_kg: document.getElementById("workout-form-weight").value,
    duration_sec: document.getElementById("workout-form-duration").value,
    notes: document.getElementById("workout-form-notes").value,
  };
  try {
    await fetchJSON("api/workouts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast("Set logged.");
    document.getElementById("workout-form-sets").value = "";
    document.getElementById("workout-form-reps").value = "";
    document.getElementById("workout-form-notes").value = "";
    loadWorkoutHistory(Number(payload.exercise_id));
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
  list.innerHTML = rows.length
    ? rows
        .map(
          (w) => `
      <li data-id="${w.id}">
        <div class="list-main">
          <div class="list-title">${escapeHtml(w.exercise_name)}</div>
          <div class="list-sub">${escapeHtml(fmtDate(w.ts))}${workoutSummary(w) ? " · " + escapeHtml(workoutSummary(w)) : ""}</div>
        </div>
        <button type="button" class="list-del workout-del" data-id="${w.id}" aria-label="Delete">✕</button>
      </li>`
        )
        .join("")
    : '<li class="empty-state">Nothing logged yet.</li>';
}

document.getElementById("workout-history").addEventListener("click", async (e) => {
  const del = e.target.closest(".workout-del");
  if (!del) return;
  try {
    await fetchJSON(`api/workouts/${del.dataset.id}`, { method: "DELETE" });
    loadWorkoutHistory(Number(document.getElementById("workout-form-exercise").value));
    loadRecentWorkouts();
  } catch (err) { toast(err.message); }
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
