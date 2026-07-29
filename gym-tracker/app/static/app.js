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
function fmtDateTime(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${hh}:${mm}`;
}
function todayISO() { return new Date().toISOString().slice(0, 10); }

// --- Home: goal card -------------------------------------------------------

// Last /api/weight payload, so the expanded chart can redraw on open and on
// resize without refetching.
let weightData = null;

async function loadHome() {
  let data;
  try {
    data = await fetchJSON("api/weight");
  } catch (e) {
    return;
  }
  weightData = data;
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
  if (document.getElementById("chart-backdrop").classList.contains("open")) {
    renderExpandedChart();
  }
}

// --- Expanded chart sheet --------------------------------------------------

// Drawn at the host's measured size rather than upscaled from the card's
// viewBox, so the extra room becomes more chart instead of bigger text.
function renderExpandedChart() {
  if (!weightData) return;
  const host = document.getElementById("weight-chart-expanded");
  const width = Math.round(host.clientWidth);
  const height = Math.round(host.clientHeight);
  if (!width || !height) return;
  renderWeightChart(weightData.logs || [], weightData.goal || {}, weightData.forecast, {
    host,
    width,
    height,
    expanded: true,
  });
}

document.getElementById("chart-expand-btn").addEventListener("click", () => {
  openSheet("chart-backdrop");
  const goal = (weightData && weightData.goal) || {};
  const hasBf = (weightData ? weightData.logs || [] : []).some((l) => l.body_fat_pct != null);
  document.getElementById("chart-sheet-title").textContent =
    hasBf ? "Weight & body fat over time" : "Weight over time";
  // Reading clientWidth/Height forces the layout the class change just
  // invalidated, so the host reports its real size here — no rAF needed.
  renderExpandedChart();
});
document.getElementById("chart-close-btn").addEventListener("click", () => closeSheet("chart-backdrop"));

// Rotating the phone changes the space available; redraw to match.
window.addEventListener("resize", () => {
  if (document.getElementById("chart-backdrop").classList.contains("open")) {
    renderExpandedChart();
  }
});

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

// --- Weight chart: stacked weight (kg) + body fat (%) panels ---------------
//
// Two panels rather than one frame with twin y-axes: kg and % have no common
// scale, so overlaying them would make the point where the lines cross — and
// their relative steepness — an artifact of the two scales. Stacked panels
// share the x-axis and the crosshair, so the trends still read together.

// `opts` lets the expanded sheet reuse this: {host, width, height} draws the
// chart at a given size in real pixels (no viewBox upscaling, so text and
// strokes stay crisp), and `expanded` earns the extra date labels that only
// have room at that size. Defaults reproduce the card exactly.
function renderWeightChart(logs, goal, forecast, opts) {
  opts = opts || {};
  const host = opts.host || document.getElementById("weight-chart");
  host.innerHTML = "";
  const series = (key) =>
    logs
      .map((l) => ({ t: new Date(l.ts).getTime(), y: l[key] }))
      .filter((p) => !isNaN(p.t) && p.y != null)
      .sort((a, b) => a.t - b.t);
  const points = series("weight_kg");
  const bfPoints = series("body_fat_pct");

  if (!points.length) {
    host.innerHTML = '<p class="empty-state">Log a weight to see your trend.</p>';
    return;
  }
  const showBf = bfPoints.length > 0;
  const bfAt = new Map(bfPoints.map((p) => [p.t, p.y]));

  const padL = 34, padR = 12, padT = 12, padB = 22;
  // Room for the body-fat panel's label between the panels. The expanded chart
  // needs more, or the two panels' nearest axis labels crowd each other.
  const labelGap = opts.expanded ? 46 : 30;
  const W = opts.width || 320;
  const H = opts.height || (showBf ? 224 : 170);
  const plotW = W - padL - padR;
  // The weight panel gives up a third of the plot height to the body-fat panel
  // when there is one. At the card's default height this is 104 / 56.
  const usableH = H - padT - padB - (showBf ? labelGap : 0);
  const wH = showBf ? Math.round(usableH * 0.65) : usableH;
  const bfTop = padT + wH + labelGap;
  const bfH = usableH - wH;
  const bottom = showBf ? bfTop + bfH : padT + wH;

  let tMin = points[0].t;
  let tMax = points[points.length - 1].t;
  if (bfPoints.length) {
    tMin = Math.min(tMin, bfPoints[0].t);
    tMax = Math.max(tMax, bfPoints[bfPoints.length - 1].t);
  }
  if (goal.target_date) {
    const td = new Date(goal.target_date).getTime();
    if (!isNaN(td)) tMax = Math.max(tMax, td);
  }
  if (tMax === tMin) tMax = tMin + 86400000;

  const sx = (t) => padL + ((t - tMin) / (tMax - tMin)) * plotW;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", showBf ? "Weight and body fat over time" : "Weight over time");

  const add = (name, attrs, cls) => {
    const e = document.createElementNS(NS, name);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (cls) e.setAttribute("class", cls);
    svg.appendChild(e);
    return e;
  };
  const label = (x, y, anchor, cls, content) => {
    const t = add("text", { x, y, "text-anchor": anchor }, cls);
    t.textContent = content;
    return t;
  };

  const defs = add("defs", {});

  // Draws one panel (gridlines, target line, dashed projection, series) and
  // returns its value->y scale so the tooltip can reuse it.
  function drawPanel(p) {
    const vals = p.points.map((q) => q.y).concat(p.target != null ? [p.target] : []);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max(0.5, (hi - lo) * 0.15);
    lo -= pad; hi += pad;
    const sy = (v) => p.top + (1 - (v - lo) / (hi - lo)) * p.height;

    // The projection is clipped to its own panel, so a trend running off the
    // bottom reads as "trending off the chart" instead of bleeding into the
    // panel below.
    // Namespaced per host: the card and the expanded sheet are in the DOM at
    // the same time, and duplicate ids would make both charts resolve
    // `url(#...)` to whichever clip path came first.
    const clipId = `${host.id || "chart"}-clip-${p.id}`;
    const clip = document.createElementNS(NS, "clipPath");
    clip.setAttribute("id", clipId);
    const cr = document.createElementNS(NS, "rect");
    cr.setAttribute("x", padL); cr.setAttribute("y", p.top);
    cr.setAttribute("width", plotW); cr.setAttribute("height", p.height);
    clip.appendChild(cr);
    defs.appendChild(clip);

    for (let i = 0; i <= p.ticks; i++) {
      const val = lo + (i / p.ticks) * (hi - lo);
      const y = sy(val);
      add("line", { x1: padL, x2: W - padR, y1: y, y2: y }, "chart-grid-line");
      label(padL - 5, y + 3, "end", "chart-axis-label", val.toFixed(0));
    }

    if (p.target != null) {
      const y = sy(p.target);
      add("line", { x1: padL, x2: W - padR, y1: y, y2: y }, "chart-target");
      label(W - padR, y - 4, "end", "chart-target-label", p.targetLabel);
    }

    // Projected trend line (dashed) — drawn under the actual line.
    if (p.trend && p.trend.length === 2) {
      const x1 = sx(p.trend[0].t), y1 = sy(p.trend[0].y);
      const x2 = sx(p.trend[1].t), y2 = sy(p.trend[1].y);
      add("line", { x1, y1, x2, y2, "clip-path": `url(#${clipId})` }, "chart-trend");
      // Label at the line's midpoint (kept inside the panel), so it never
      // collides with the "Target" label pinned to the top-right. Sit it above
      // the trend, unless that lands it on the target line — where the trend
      // crosses the target, which is exactly the interesting case, so flip
      // below rather than let the two labels overlap.
      const mx = (clamp(x1, padL, W - padR) + clamp(x2, padL, W - padR)) / 2;
      const mid = (y1 + y2) / 2;
      const above = mid - 5;
      // Flip below the trend if sitting above it would put the label on the
      // target line, or on the logged series itself — which happens on the
      // body-fat panel, where the readings stop around the trend's midpoint.
      const near = p.points.reduce(
        (best, q) => (Math.abs(sx(q.t) - mx) < Math.abs(sx(best.t) - mx) ? q : best),
        p.points[0]
      );
      const flip =
        (p.target != null && Math.abs(above - sy(p.target)) < 10) ||
        (Math.abs(sx(near.t) - mx) < 45 && Math.abs(sy(near.y) - above) < 14);
      // Clearing a sloped line by a fixed 12px isn't enough: over the label's
      // half-width the line has already dropped back into it, so scale the
      // offset by the slope.
      const slope = x2 === x1 ? 0 : Math.abs((y2 - y1) / (x2 - x1));
      const below = mid + clamp(12 + slope * 45, 12, 30);
      const my = clamp(flip ? below : above, p.top + 10, p.top + p.height - 4);
      label(mx, my, "middle", "chart-trend-label", p.trendLabel);
    }

    if (p.points.length > 1) {
      add("polyline", { points: p.points.map((q) => `${sx(q.t)},${sy(q.y)}`).join(" ") }, p.lineClass);
    }
    p.points.forEach((q) => add("circle", { cx: sx(q.t), cy: sy(q.y), r: 3.5 }, p.markerClass));
    return sy;
  }

  const fc = forecast || {};
  const target = goal.target_weight_kg;
  // One gridline per ~45px of panel height: gives the card its 2 (or 3, with
  // no body-fat panel) and fills the taller expanded panels without crowding.
  const ticksFor = (h) => clamp(Math.round(h / 45), 2, 5);
  const sy = drawPanel({
    id: "weight",
    top: padT,
    height: wH,
    ticks: ticksFor(wH),
    points,
    target,
    targetLabel: `Target ${target}`,
    lineClass: "chart-line",
    markerClass: "chart-marker",
    trend:
      fc.available && fc.trend && fc.trend.length === 2
        ? fc.trend.map((q) => ({ t: new Date(q.ts).getTime(), y: q.weight_kg }))
        : null,
    trendLabel: "Projected",
  });

  let syBf = null;
  if (showBf) {
    // Panel label doubles as the legend: the marker carries the series colour,
    // the text names it, so identity is never colour-alone.
    add("circle", { cx: padL + 4, cy: bfTop - 13, r: 3.5 }, "chart-marker-bf");
    label(
      padL + 12, bfTop - 10, "start", "chart-panel-label",
      goal.target_body_fat_pct != null
        ? `Body fat % · target ${goal.target_body_fat_pct} %`
        : "Body fat %"
    );
    const bfTarget = goal.target_body_fat_pct;
    syBf = drawPanel({
      id: "bf",
      top: bfTop,
      height: bfH,
      ticks: ticksFor(bfH),
      points: bfPoints,
      target: bfTarget != null ? bfTarget : null,
      targetLabel: `Target ${bfTarget} %`,
      lineClass: "chart-line-bf",
      markerClass: "chart-marker-bf",
      trend:
        fc.bf_available && fc.bf_trend && fc.bf_trend.length === 2
          ? fc.bf_trend.map((q) => ({ t: new Date(q.ts).getTime(), y: q.body_fat_pct }))
          : null,
      trendLabel: fc.bf_available ? `Projected ${fc.bf_projected_pct} %` : "Projected",
    });
  }

  // x labels: first + last, shared by both panels. The expanded chart is wide
  // enough for dates in between, one per ~120px.
  const xlbl = (t, anchor, x) =>
    label(x, H - 6, anchor, "chart-axis-label", fmtDate(new Date(t).toISOString()));
  xlbl(tMin, "start", padL);
  xlbl(tMax, "end", W - padR);
  const xTicks = opts.expanded ? clamp(Math.round(plotW / 120), 2, 6) : 1;
  for (let i = 1; i < xTicks; i++) {
    const t = tMin + (i / xTicks) * (tMax - tMin);
    xlbl(t, "middle", padL + (i / xTicks) * plotW);
  }

  // Hover crosshair + tooltip — one crosshair spanning both panels.
  const cross = add("line", { y1: padT, y2: bottom }, "chart-crosshair");
  cross.style.opacity = "0";
  const hit = add(
    "rect",
    { x: padL, y: padT, width: plotW, height: bottom - padT },
    "chart-hit"
  );

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
    const bf = bfAt.get(nearest.t);
    tip.innerHTML =
      `<strong>${nearest.y} kg</strong>` +
      (bf != null ? ` · <strong>${bf} %</strong>` : "") +
      `<br>${escapeHtml(fmtDate(new Date(nearest.t).toISOString()))}`;
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

// Last /api/challenge payload — the source for optimistic toggles between
// server round-trips. Rendering reads from here so an optimistic tweak to the
// cache shows up the moment we re-render, before the network answers.
let challengeData = null;

async function loadChallenge() {
  try { challengeData = await fetchJSON("api/challenge"); } catch (e) { return; }
  renderChallenge(challengeData);
}

function renderChallenge(data) {
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

document.getElementById("challenge-list").addEventListener("click", (e) => {
  const el = e.target.closest(".challenge-item");
  if (!el || !challengeData) return;
  const item = (challengeData.items || []).find((it) => it.id === Number(el.dataset.id));
  if (!item) return;

  // Optimistic update: flip the check now (and, for exercise items, the Recent
  // workouts card, since ticking one logs a workout) so the UI reacts instantly
  // like a live app. The POST reconciles against the server; on failure we roll
  // the same change back and tell the user.
  const nextDone = !item.done_today;
  applyChallengeToggle(item, nextDone);

  fetchJSON("api/challenge/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: item.id }),
  })
    .then(() => {
      // Server is the source of truth — re-sync streak, week dots and the exact
      // workout rows (real ids, ordering) the optimistic pass approximated.
      loadChallenge();
      refreshWorkoutViews();
    })
    .catch(() => {
      applyChallengeToggle(item, !nextDone);
      toast("Couldn't update — check your connection.");
    });
});

// Apply a challenge toggle to the local caches and re-render — no network. For
// exercise items this also mirrors the auto-logged workout in the Recent
// workouts card so it tracks the check optimistically.
function applyChallengeToggle(item, done) {
  item.done_today = done;
  renderChallenge(challengeData);

  if (item.item_type !== "exercise") return;
  const day = challengeData.today;
  const key = `challenge-${item.id}-${day}`;
  // Remove any existing row for this exercise+day (an earlier optimistic row, or
  // the real server row when un-ticking), then re-add if the item is now done.
  const isThisRow = (w) =>
    w._optimisticKey === key ||
    (w.source === "challenge" && w.exercise_id === item.exercise_id && String(w.ts).slice(0, 10) === day);
  recentWorkoutsCache = recentWorkoutsCache.filter((w) => !isThisRow(w));
  if (done) {
    recentWorkoutsCache.unshift({
      _optimisticKey: key,
      ts: `${day}T12:00:00`,
      exercise_id: item.exercise_id,
      exercise_name: item.name,
      sets: item.target_sets,
      reps: item.target_reps,
      weight_kg: null,
      duration_sec: null,
      source: "challenge",
    });
  }
  renderRecentWorkouts(recentWorkoutsCache);
}

// Keep every view of the workout log current: the home "Recent workouts" card
// and, when the workouts sheet happens to be open, its history list.
function refreshWorkoutViews() {
  loadRecentWorkouts();
  const backdrop = document.getElementById("workout-backdrop");
  if (backdrop && backdrop.classList.contains("open")) loadWorkoutHistory(workoutFilterExerciseId);
}

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

// Last /api/challenge/history payload — the source for optimistic cell toggles.
let challengeHistoryData = null;

async function loadChallengeHistory() {
  const from = document.getElementById("history-from").value;
  const to = document.getElementById("history-to").value;
  const qs = from && to ? `from=${from}&to=${to}` : "days=14";
  try { challengeHistoryData = await fetchJSON(`api/challenge/history?${qs}`); } catch (e) { return; }
  renderChallengeHistory(challengeHistoryData);
}

function renderChallengeHistory(data) {
  const host = document.getElementById("history-grid");
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

document.getElementById("history-grid").addEventListener("click", (e) => {
  const cell = e.target.closest(".history-cell");
  if (!cell || !challengeHistoryData) return;
  const itemId = Number(cell.dataset.item);
  const day = cell.dataset.day;
  const dayEntry = (challengeHistoryData.days || []).find((d) => d.day === day);
  if (!dayEntry) return;

  // Optimistic update: flip this cell and the row's "complete" state from the
  // cache now, so the grid reacts instantly. The POST reconciles against the
  // server; on failure we flip it back and tell the user.
  const wasDone = dayEntry.done.includes(itemId);
  applyHistoryToggle(dayEntry, itemId, !wasDone);

  fetchJSON("api/challenge/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_id: itemId, day }),
  })
    .then(() => {
      // Server is the source of truth for the grid, and — since ticking an
      // exercise on any day logs or removes a workout — the challenge card and
      // workout lists underneath the sheet too.
      loadChallengeHistory();
      loadChallenge();
      refreshWorkoutViews();
    })
    .catch(() => {
      applyHistoryToggle(dayEntry, itemId, wasDone);
      toast("Couldn't update — check your connection.");
    });
});

// Flip one day/item completion in the history cache, recompute that day's
// "complete" flag (all active items done — matching the server), and re-render.
function applyHistoryToggle(dayEntry, itemId, done) {
  dayEntry.done = dayEntry.done.filter((id) => id !== itemId);
  if (done) dayEntry.done.push(itemId);
  const activeIds = (challengeHistoryData.items || []).map((it) => it.id);
  dayEntry.complete = activeIds.length > 0 && activeIds.every((id) => dayEntry.done.includes(id));
  renderChallengeHistory(challengeHistoryData);
}

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
  // Pre-fill the device with the last one used, so repeat weigh-ins on the same
  // scale are one tap; the datalist still offers any other devices.
  document.getElementById("weight-form-device").value = window._lastWeightDevice || "";
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
    device: document.getElementById("weight-form-device").value,
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
      const device = l.device ? ` · ⚖ ${escapeHtml(l.device)}` : "";
      const note = l.notes ? ` · ${escapeHtml(l.notes)}` : "";
      return `
        <li data-id="${l.id}">
          <div class="list-main">
            <div class="list-title">${l.weight_kg} kg</div>
            <div class="list-sub">${escapeHtml(fmtDate(l.ts))}${bf}${device}${note}</div>
          </div>
          <div>
            <button type="button" class="link-btn weight-edit" data-id="${l.id}">Edit</button>
            <button type="button" class="list-del weight-del" data-id="${l.id}" aria-label="Delete">✕</button>
          </div>
        </li>`;
    })
    .join("");
  window._weightLogs = data.logs || [];

  // Datalist of devices already used (most recent first), and remember the
  // latest for pre-filling new entries.
  const seen = [];
  for (const l of logs) {
    if (l.device && !seen.includes(l.device)) seen.push(l.device);
  }
  window._lastWeightDevice = seen[0] || "";
  document.getElementById("weight-device-list").innerHTML = seen
    .map((d) => `<option value="${escapeHtml(d)}"></option>`)
    .join("");
  // If the form is on a fresh entry, keep its device default in sync.
  if (!document.getElementById("weight-form-id").value &&
      !document.getElementById("weight-form-device").value) {
    document.getElementById("weight-form-device").value = window._lastWeightDevice;
  }
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
    document.getElementById("weight-form-device").value = log.device || "";
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
let supplementTimingsLoaded = false;

async function ensureTimingOptions() {
  if (supplementTimingsLoaded) return;
  let timings = [];
  try { timings = await fetchJSON("api/supplement-timings"); } catch (e) { /* ignore */ }
  document.getElementById("supplement-form-timing").innerHTML =
    '<option value="">—</option>' + timings.map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join("");
  supplementTimingsLoaded = true;
}

function resetSupplementForm() {
  document.getElementById("supplement-form-id").value = "";
  document.getElementById("supplement-form-name").value = "";
  document.getElementById("supplement-form-amount").value = "";
  document.getElementById("supplement-form-unit").value = "";
  document.getElementById("supplement-form-qty").value = "";
  document.getElementById("supplement-form-timing").value = "";
  document.getElementById("supplement-form-brand").value = "";
  document.getElementById("supplement-form-save").textContent = "Add supplement";
  document.getElementById("supplement-form-cancel").hidden = true;
}

async function loadSupplements() {
  await ensureTimingOptions();
  try { supplementsCache = await fetchJSON("api/supplements"); } catch (e) { return; }
  document.getElementById("supplements-list").innerHTML = supplementsCache.length
    ? supplementsCache
        .map((s) => {
          const meta = [s.timing, s.brand].filter(Boolean).map(escapeHtml).join(" · ");
          return `
        <li data-id="${s.id}">
          <span class="ci-icon">💊</span>
          <span class="ci-name">${escapeHtml(s.name)}${s.dose ? ` <span class="muted">· ${escapeHtml(s.dose)}</span>` : ""}
            ${meta ? `<br><span class="list-sub">${meta}</span>` : ""}</span>
          <button type="button" class="link-btn sup-edit" data-id="${s.id}" aria-label="Edit">✎</button>
          <button type="button" class="list-del sup-del" data-id="${s.id}" aria-label="Remove">✕</button>
        </li>`;
        })
        .join("")
    : '<li class="empty-state">No supplements yet.</li>';
}

document.getElementById("supplement-form-cancel").addEventListener("click", resetSupplementForm);

document.getElementById("supplement-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("supplement-form-id").value;
  const payload = {
    name: document.getElementById("supplement-form-name").value.trim(),
    dose_amount: document.getElementById("supplement-form-amount").value,
    dose_unit: document.getElementById("supplement-form-unit").value.trim(),
    quantity: document.getElementById("supplement-form-qty").value,
    timing: document.getElementById("supplement-form-timing").value,
    brand: document.getElementById("supplement-form-brand").value.trim(),
  };
  if (!payload.name) return;
  try {
    await fetchJSON(id ? `api/supplements/${id}` : "api/supplements", {
      method: id ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    resetSupplementForm();
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
    document.getElementById("supplement-form-id").value = s.id;
    document.getElementById("supplement-form-name").value = s.name;
    document.getElementById("supplement-form-amount").value = s.dose_amount != null ? s.dose_amount : "";
    document.getElementById("supplement-form-unit").value = s.dose_unit || "";
    document.getElementById("supplement-form-qty").value = s.quantity != null ? s.quantity : "";
    document.getElementById("supplement-form-timing").value = s.timing || "";
    document.getElementById("supplement-form-brand").value = s.brand || "";
    document.getElementById("supplement-form-save").textContent = "Save supplement";
    document.getElementById("supplement-form-cancel").hidden = false;
    document.getElementById("supplement-form-name").focus();
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

// Full recent-workout list from the server (up to the API's 200). Keeping more
// than the five shown lets an optimistic un-tick reveal the next real row
// underneath instead of leaving a gap. renderRecentWorkouts() shows the top 5.
let recentWorkoutsCache = [];

async function loadRecentWorkouts() {
  let rows;
  try { rows = await fetchJSON("api/workouts"); } catch (e) { return; }
  recentWorkoutsCache = rows;
  renderRecentWorkouts(recentWorkoutsCache);
}

function renderRecentWorkouts(rows) {
  const list = document.getElementById("recent-workout-list");
  const empty = document.getElementById("recent-workout-empty");
  const top = rows.slice(0, 5);
  empty.hidden = top.length > 0;
  list.innerHTML = top
    .map(
      (w) => `
      <li>
        <div class="list-main">
          <div class="list-title">${escapeHtml(w.exercise_name)}</div>
          <div class="list-sub">${escapeHtml(fmtDate(w.ts))}${workoutSummary(w) ? " · " + escapeHtml(workoutSummary(w)) : ""}</div>
          ${w.notes ? `<div class="list-note">${escapeHtml(w.notes)}</div>` : ""}
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

// --- Garmin Connect --------------------------------------------------------

function fmtDuration(sec) {
  if (sec == null) return null;
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function garminActivitySummary(a) {
  const bits = [];
  if (a.duration_sec != null) bits.push(fmtDuration(a.duration_sec));
  if (a.distance_m != null) bits.push(`${(a.distance_m / 1000).toFixed(2)} km`);
  if (a.calories != null) bits.push(`${a.calories} kcal`);
  if (a.avg_hr != null) bits.push(`♥ ${a.avg_hr}`);
  return bits.join(" · ");
}

function prettyActivityType(t) {
  if (!t) return "Activity";
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Rough "how long ago", for sync freshness. Accepts a date or a timestamp.
function fmtAgo(iso) {
  const then = new Date(iso.length <= 10 ? `${iso}T12:00:00` : iso);
  if (isNaN(then)) return "";
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 36) return `${hours}h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

// Populate the home card from api/garmin/summary.
async function loadGarminCard() {
  const tiles = document.getElementById("garmin-tiles");
  const list = document.getElementById("garmin-activity-list");
  const empty = document.getElementById("garmin-empty");
  const cardBtn = document.getElementById("garmin-card-btn");
  let data;
  try { data = await fetchJSON("api/garmin/summary"); } catch (e) { return; }

  cardBtn.textContent = data.connected ? "Manage" : "Connect";
  const d = data.latest;
  const hasContent = data.connected && (d || (data.activities || []).length);

  // A watch that hasn't uploaded leaves the newest stored day sitting in the
  // past; showing it undated would read as "today".
  const asOf = document.getElementById("garmin-asof");
  asOf.hidden = !(d && d.day && d.day !== todayISO());
  if (!asOf.hidden) asOf.textContent = `As of ${fmtDate(d.day)} · ${fmtAgo(d.day)}`;

  tiles.hidden = !d;
  if (d) {
    document.getElementById("garmin-sleep").textContent = fmtDuration(d.sleep_seconds) || "–";
    document.getElementById("garmin-stress").textContent = d.stress_avg != null ? d.stress_avg : "–";
    document.getElementById("garmin-battery").textContent =
      d.body_battery_high != null ? `${d.body_battery_low != null ? d.body_battery_low + "–" : ""}${d.body_battery_high}` : "–";
  }

  list.innerHTML = (data.activities || [])
    .slice(0, 5)
    .map(
      (a) => `
      <li>
        <div class="list-main">
          <div class="list-title">${escapeHtml(a.name || prettyActivityType(a.activity_type))}</div>
          <div class="list-sub">${escapeHtml(fmtDate(a.start_time))}${garminActivitySummary(a) ? " · " + escapeHtml(garminActivitySummary(a)) : ""}</div>
        </div>
      </li>`
    )
    .join("");

  empty.hidden = !!hasContent;
  if (!data.connected) empty.textContent = "Connect Garmin to see your sleep, stress, Body Battery and activities.";
  else if (!hasContent) empty.textContent = "Connected. Press Sync to pull your latest Garmin data.";
}

// The three stored daily metrics, one shown at a time. Only one series is on
// screen at once, so colour carries no identity here — the chip does.
const GARMIN_METRICS = {
  sleep: { key: "sleep_seconds", max: 9 * 3600, fmt: (v) => fmtDuration(v) },
  stress: { key: "stress_avg", max: 100, fmt: (v) => `${v}` },
  battery: { key: "body_battery_high", max: 100, fmt: (v) => `${v}` },
};
const GARMIN_HISTORY_DAYS = 14;
let garminMetric = "sleep";
let garminDeviceUpload = null;

// A day with nothing stored is drawn as a gap, never as a zero-length bar —
// and it says which kind of nothing it is: a day the watch hasn't uploaded yet
// reads differently from a day the watch simply wasn't worn.
async function renderGarminHistory() {
  const host = document.getElementById("garmin-history");
  const from = new Date(Date.now() - (GARMIN_HISTORY_DAYS - 1) * 86400000)
    .toISOString()
    .slice(0, 10);
  let rows;
  try {
    rows = await fetchJSON(`api/garmin/daily?from=${from}`);
  } catch (e) {
    host.innerHTML = "";
    return;
  }
  const byDay = new Map((rows || []).map((r) => [r.day, r]));
  const metric = GARMIN_METRICS[garminMetric];
  const uploadedThrough = garminDeviceUpload ? garminDeviceUpload.slice(0, 10) : null;

  const values = (rows || []).map((r) => r[metric.key]).filter((v) => v != null);
  const scale = Math.max(metric.max, ...values) || 1;

  const out = [];
  for (let i = 0; i < GARMIN_HISTORY_DAYS; i++) {
    const day = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    const row = byDay.get(day);
    const value = row ? row[metric.key] : null;
    const label = `<span class="ghist-day">${escapeHtml(fmtDate(day))}</span>`;
    if (value == null) {
      const why = uploadedThrough && day > uploadedThrough ? "not synced" : "no data";
      out.push(
        `<div class="ghist-row ghist-gap">${label}` +
          `<span class="ghist-bar"><span class="ghist-none"></span></span>` +
          `<span class="ghist-val">${why}</span></div>`
      );
    } else {
      const pct = Math.max(2, Math.min(100, (value / scale) * 100));
      out.push(
        `<div class="ghist-row">${label}` +
          `<span class="ghist-bar"><span class="ghist-fill" style="width:${pct}%"></span></span>` +
          `<span class="ghist-val">${escapeHtml(metric.fmt(value))}</span></div>`
      );
    }
  }
  host.innerHTML = out.join("");
}

document.getElementById("garmin-metric-chips").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-metric]");
  if (!btn) return;
  garminMetric = btn.dataset.metric;
  document.querySelectorAll("#garmin-metric-chips .chip").forEach((c) => {
    c.classList.toggle("chip-on", c === btn);
  });
  renderGarminHistory();
});

// Populate the Garmin sheet (status + which controls to show).
async function loadGarmin() {
  let data;
  try { data = await fetchJSON("api/garmin/status"); } catch (e) { return; }
  garminDeviceUpload = data.device_last_upload || null;
  const badge = (on) => `<span class="badge ${on ? "badge-on" : "badge-off"}">${on ? "Connected" : "Not connected"}</span>`;
  const parts = [`<div class="reminder-line">${badge(data.connected)}<span>Garmin account</span></div>`];
  if (data.connected) {
    const last = data.last_sync ? fmtDateTime(data.last_sync) : "never";
    parts.push(`<div class="reminder-line"><span>Last sync: ${escapeHtml(last)}${data.auto_sync ? ` · auto every ${data.interval_hours}h` : " · auto-sync off"}</span></div>`);
    // The add-on can be syncing happily against a watch that stopped
    // uploading days ago, so the two are reported separately.
    if (data.device_last_upload) {
      const ago = fmtAgo(data.device_last_upload);
      const stale = Date.now() - new Date(data.device_last_upload).getTime() > 36 * 3600 * 1000;
      parts.push(
        `<div class="reminder-line"><span${stale ? ' class="garmin-warn"' : ""}>` +
          `Watch last uploaded: ${escapeHtml(fmtDateTime(data.device_last_upload))} · ${escapeHtml(ago)}` +
          `${stale ? " — sync your watch to fill the gap" : ""}</span></div>`
      );
    }
    if (data.last_error) parts.push(`<div class="reminder-line"><span class="garmin-error">Last error: ${escapeHtml(data.last_error)}</span></div>`);
  }
  document.getElementById("garmin-status").innerHTML = parts.join("");
  if (data.connected) renderGarminHistory();

  document.getElementById("garmin-login-form").hidden = data.connected;
  document.getElementById("garmin-connected").hidden = !data.connected;
  // Reset the login form between opens.
  if (!data.connected) {
    document.getElementById("garmin-mfa-field").hidden = true;
    document.getElementById("garmin-login-btn").textContent = "Connect";
  }
}

document.getElementById("garmin-open-btn").addEventListener("click", () => {
  openSheet("garmin-backdrop");
  loadGarmin();
});
document.getElementById("garmin-card-btn").addEventListener("click", () => {
  openSheet("garmin-backdrop");
  loadGarmin();
});
document.getElementById("garmin-close-btn").addEventListener("click", () => {
  closeSheet("garmin-backdrop");
  loadGarminCard();
});

// Login handles both the credential step and the follow-up 2FA code step. When
// the server answers "mfa_required" we reveal the code field and the same
// submit sends the code to api/garmin/mfa.
document.getElementById("garmin-login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = document.getElementById("garmin-login-result");
  const mfaField = document.getElementById("garmin-mfa-field");
  const code = document.getElementById("garmin-mfa-code").value.trim();
  const btn = document.getElementById("garmin-login-btn");

  try {
    let res;
    if (!mfaField.hidden && code) {
      result.textContent = "Verifying code…";
      res = await fetchJSON("api/garmin/mfa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
    } else {
      result.textContent = "Connecting…";
      res = await fetchJSON("api/garmin/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("garmin-email").value.trim(),
          password: document.getElementById("garmin-password").value,
        }),
      });
    }
    if (res.status === "mfa_required") {
      mfaField.hidden = false;
      btn.textContent = "Verify code";
      result.textContent = "Enter the 2-factor code Garmin just sent you.";
      document.getElementById("garmin-mfa-code").focus();
      return;
    }
    // Connected.
    document.getElementById("garmin-password").value = "";
    document.getElementById("garmin-mfa-code").value = "";
    result.textContent = "Connected. Syncing…";
    await loadGarmin();
    try { await fetchJSON("api/garmin/sync", { method: "POST" }); } catch (err) { /* first sync best-effort */ }
    result.textContent = "";
    loadGarminCard();
  } catch (err) {
    result.textContent = err.message;
  }
});

document.getElementById("garmin-sync-btn").addEventListener("click", async () => {
  const result = document.getElementById("garmin-sync-result");
  result.textContent = "Syncing…";
  try {
    const res = await fetchJSON("api/garmin/sync", { method: "POST" });
    const imp = res.imported || {};
    const back = imp.backfilled ? `, ${imp.backfilled} backfilled` : "";
    result.textContent = `Synced ${imp.days || 0} days${back}, ${imp.activities || 0} activities.`;
    loadGarmin();
    loadGarminCard();
  } catch (err) {
    result.textContent = `Sync failed: ${err.message}`;
  }
});

document.getElementById("garmin-disconnect-btn").addEventListener("click", async () => {
  if (!confirm("Disconnect Garmin? Imported data stays; you'll need to sign in again to sync.")) return;
  try {
    await fetchJSON("api/garmin/disconnect", { method: "POST" });
    loadGarmin();
    loadGarminCard();
  } catch (err) { toast(err.message); }
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
loadGarminCard();
pingStatus();
