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
// 2600ms was tuned for short, glanceable text ("Set logged."). A stoic quote
// runs three or four times as long, so the on-screen time scales with the
// message rather than every toast racing the same fixed clock. `multiplier`
// is an escape hatch for a specific caller that needs longer than the normal
// formula gives it (see celebrateDay) without changing the clock every other
// toast in the app races against.
function toast(msg, { multiplier = 1 } = {}) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  const duration = Math.min(7000, Math.max(2600, msg.length * 60)) * multiplier;
  toastTimer = setTimeout(() => { el.hidden = true; }, duration);
}

// --- Celebrations -----------------------------------------------------------
// Finishing is the point of a challenge, so it should feel like something. Two
// weights: a burst when the day is done, and a proper moment when a whole
// challenge is completed — the daily one stays small so it doesn't wear out.

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// Served by the app (window.GYM.quotes) so the celebration toast and the daily
// quote notification always draw on the same list; the fallback only matters if
// the page somehow renders without it.
const STOIC_QUOTES = (window.GYM && window.GYM.quotes && window.GYM.quotes.length)
  ? window.GYM.quotes
  : [["You have power over your mind — not outside events.", "Marcus Aurelius"]];

let lastQuoteIndex = -1;
function pickQuote() {
  if (STOIC_QUOTES.length < 2) return STOIC_QUOTES[0];
  let i;
  do { i = Math.floor(Math.random() * STOIC_QUOTES.length); } while (i === lastQuoteIndex);
  lastQuoteIndex = i;
  return STOIC_QUOTES[i];
}

function confetti(count = 90) {
  // Hand-rolled rather than a library: the page is served from an add-on with
  // no CDN access, and this is ~30 lines. Skipped entirely for anyone who has
  // asked for less motion — they still get the message.
  if (reducedMotion.matches) return;
  const canvas = document.getElementById("confetti");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * dpr;
  canvas.height = canvas.clientHeight * dpr;
  ctx.scale(dpr, dpr);
  canvas.hidden = false;

  const w = canvas.clientWidth;
  const colors = ["#6c8cff", "#7ee2b8", "#ffd166", "#ff8fa3", "#c3a6ff"];
  const bits = Array.from({ length: count }, () => ({
    x: w / 2 + (Math.random() - 0.5) * w * 0.5,
    y: canvas.clientHeight * 0.35 + (Math.random() - 0.5) * 60,
    vx: (Math.random() - 0.5) * 9,
    vy: Math.random() * -11 - 3,
    rot: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.3,
    size: 5 + Math.random() * 6,
    color: colors[Math.floor(Math.random() * colors.length)],
  }));

  let frames = 0;
  (function tick() {
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    let alive = false;
    for (const b of bits) {
      b.vy += 0.32;           // gravity
      b.vx *= 0.99;           // drag
      b.x += b.vx;
      b.y += b.vy;
      b.rot += b.vr;
      if (b.y < canvas.clientHeight + 20) alive = true;
      ctx.save();
      ctx.translate(b.x, b.y);
      ctx.rotate(b.rot);
      ctx.fillStyle = b.color;
      ctx.globalAlpha = Math.max(0, 1 - frames / 150);
      ctx.fillRect(-b.size / 2, -b.size / 4, b.size, b.size / 2);
      ctx.restore();
    }
    frames += 1;
    if (alive && frames < 160) requestAnimationFrame(tick);
    else { canvas.hidden = true; ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight); }
  })();
}

function celebrateDay(challenge) {
  confetti(60);
  const streak = challenge && challenge.streak;
  const [text, author] = pickQuote();
  const streakPart = streak > 1 ? ` · ${streak} day streak 🔥` : "";
  // 2x the normal clock — even at 1.35.1's up-to-7s cap, a full quote plus a
  // streak line was still gone before some people finished reading it.
  toast(`“${text}” — ${author}${streakPart}`, { multiplier: 2 });
}

// Every challenge due today, fully ticked — the moment the checklist itself
// can't beat, so it gets the modal rather than the toast the daily one uses.
function challengesDueToday(list) {
  return (list || []).filter(
    (c) =>
      !c.finished &&
      !c.not_started &&
      c.due_today !== false &&
      (c.items || []).some((i) => !i.archived)
  );
}

function allDueChallengesComplete(list) {
  const due = challengesDueToday(list);
  return due.length > 0 && due.every(isChallengeComplete);
}

function celebrateAllDone(list) {
  const due = challengesDueToday(list);
  const items = due.reduce((n, c) => n + (c.items || []).filter((i) => !i.archived).length, 0);
  const el = document.getElementById("celebration");
  el.classList.add("celebration-grand");
  el.querySelector(".celebration-emoji").textContent = "⚡";
  document.getElementById("celebration-title").textContent = "All done for today";
  el.querySelector(".celebration-name").textContent = "Every box, ticked";
  el.querySelector(".celebration-stats").textContent =
    `${due.length} challenge${due.length === 1 ? "" : "s"} · ${items} item${items === 1 ? "" : "s"}`;
  const [text, author] = pickQuote();
  const quoteEl = document.getElementById("celebration-quote");
  quoteEl.textContent = `“${text}” — ${author}`;
  quoteEl.hidden = false;
  el.hidden = false;
  confetti(200);
  if (!reducedMotion.matches) setTimeout(() => confetti(140), 260);
}

function celebrateChallenge(view, stats) {
  const el = document.getElementById("celebration");
  el.classList.remove("celebration-grand");
  el.querySelector(".celebration-emoji").textContent = "🏆";
  document.getElementById("celebration-title").textContent = "Challenge complete";
  document.getElementById("celebration-quote").hidden = true;
  const pct = stats && stats.completion_pct != null ? `${stats.completion_pct}%` : null;
  const lines = [];
  if (stats) {
    lines.push(`${stats.days_complete} of ${stats.days_elapsed} days${pct ? ` · ${pct}` : ""}`);
    if (stats.longest_streak) lines.push(`longest streak ${stats.longest_streak}`);
  }
  el.querySelector(".celebration-name").textContent = view.name;
  el.querySelector(".celebration-stats").textContent = lines.join(" · ");
  el.hidden = false;
  confetti(160);
}

function dismissCelebration() {
  const el = document.getElementById("celebration");
  el.hidden = true;
  el.classList.remove("celebration-grand");
  const quoteEl = document.getElementById("celebration-quote");
  quoteEl.hidden = true;
  quoteEl.textContent = "";
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

// --- Tabs ------------------------------------------------------------------

const TAB_STORAGE_KEY = "gym.tab";

function showTab(panelId) {
  document.querySelectorAll("#main-tabs .tab").forEach((btn) => {
    const on = btn.dataset.panel === panelId;
    btn.classList.toggle("tab-on", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== panelId;
  });
  // Ingress can run without storage; remembering the tab is not worth failing over.
  try { localStorage.setItem(TAB_STORAGE_KEY, panelId); } catch (e) { /* ignore */ }
}

document.getElementById("main-tabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".tab");
  if (!btn) return;
  showTab(btn.dataset.panel);
  if (btn.dataset.panel === "panel-trends") { loadChallengeStats(); loadSessions(); }
});

(function restoreTab() {
  let saved = null;
  try { saved = localStorage.getItem(TAB_STORAGE_KEY); } catch (e) { /* ignore */ }
  if (saved && document.getElementById(saved)) showTab(saved);
})();

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
  const daysText = days == null ? "—" : days >= 0 ? `${days} days left` : `${-days} days over`;
  document.getElementById("goal-days").textContent = daysText;
  document.getElementById("trends-days").textContent = daysText;

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

// Last /api/challenges payload — the source for optimistic toggles between
// server round-trips. Rendering reads from here so an optimistic tweak to the
// cache shows up the moment we re-render, before the network answers.
let challengeData = [];
// Which challenge the items sheet is editing.
let challengeItemsFor = null;

async function loadChallenge() {
  try { challengeData = await fetchJSON("api/challenges"); } catch (e) { return; }
  renderChallenge(challengeData);
  maybeCelebrateFinished();
}

// A challenge that ran out while the app was closed still deserves its moment,
// so this is driven by the server's `awaiting_celebration` rather than by
// noticing the transition live. Marked seen only once shown, so a reload does
// not replay it and a missed one is not lost.
let celebrationPending = false;
async function maybeCelebrateFinished() {
  if (celebrationPending) return;
  const done = (challengeData || []).find((c) => c.awaiting_celebration);
  if (!done) return;
  celebrationPending = true;
  let stats = null;
  try {
    const all = await fetchJSON("api/challenges/stats");
    stats = (all || []).find((s) => s.id === done.id) || null;
  } catch (e) { /* the numbers are a bonus; the moment is not */ }
  celebrateChallenge(done, stats);
  try {
    await fetchJSON(`api/challenges/${done.id}/celebrated`, { method: "POST" });
    done.awaiting_celebration = false;
  } catch (e) {
    // Unmarked, so it will be offered again next load rather than lost.
  }
  celebrationPending = false;
}

// Computed locally so the optimistic tick can tell the moment it lands, rather
// than waiting for the server to say so.
function isChallengeComplete(challenge) {
  const items = (challenge && challenge.items) || [];
  const live = items.filter((i) => !i.archived);
  return live.length > 0 && live.every((i) => i.done_today);
}

function challengeById(id) {
  return (challengeData || []).find((c) => c.id === id) || null;
}

// Names what actually goes, rather than a bare "are you sure?".
function undoTickPrompt(item, day) {
  const when = day ? ` on ${fmtDate(day)}` : "";
  const loses =
    item.item_type === "exercise"
      ? " This also removes the workout it logged."
      : "";
  return `Un-tick ${item.label || item.name}${when}?${loses}`;
}

function findChallengeItem(itemId) {
  for (const ch of challengeData || []) {
    const item = (ch.items || []).find((it) => it.id === itemId);
    if (item) return { challenge: ch, item };
  }
  return null;
}

// Which resting cards the reader has unfolded. Kept outside the render because
// every tick re-renders the whole list: without this, ticking a bonus item on a
// rest day would fold the list shut under your finger.
const restExpanded = new Set();

function renderChallenge(list) {
  const host = document.getElementById("challenge-cards");
  // A finished challenge drops off Home; its statistics stay on Trends.
  const running = (list || []).filter((c) => !c.finished);
  if (!running.length) {
    host.innerHTML =
      '<section class="card"><p class="empty-state">No challenges running. Create one to start a streak.</p></section>';
    return;
  }
  host.innerHTML = running.map(challengeCardHtml).join("");
}

function challengeCardHtml(ch) {
  const items = (ch.items || [])
    .map(
      (it) => `
      <li class="challenge-item ${it.done_today ? "done" : ""}" data-id="${it.id}">
        <span class="challenge-check">${it.done_today ? "✓" : ""}</span>
        ${it.item_type === "exercise" && it.image_v
          ? `<img class="ci-thumb" src="${exerciseImageUrl(it.exercise_id, it.image_v)}" alt="" loading="lazy">`
          : ""}
        <span class="challenge-label">${escapeHtml(it.label)}</span>
        ${it.is_routine
          ? `<button type="button" class="link-btn ci-play" data-exercise="${it.exercise_id}"
                     data-item="${it.id}" aria-label="Start this routine"
                     title="Count me through it">▶</button>`
          : ""}
      </li>`
    )
    .join("");
  const dots = (ch.last_7_days || [])
    .map(
      (d) => `<span class="week-dot ${d.complete ? "on" : ""} ${d.scheduled === false ? "rest" : ""} ${d.day === ch.today ? "today" : ""}" title="${d.day}${d.scheduled === false ? " · rest day" : ""}"></span>`
    )
    .join("");
  // A rest day says so, and says when it is next due — nothing is owed today.
  const progress = ch.not_started
    ? `starts ${escapeHtml(fmtDate(ch.start_date))}`
    : ch.due_today === false
    ? `Rest day${ch.next_due ? ` · next ${escapeHtml(fmtDate(ch.next_due))}` : ""}`
    : ch.total_days
    ? `day ${ch.day_number} of ${ch.total_days}`
    : "";
  const empty = items ? "" : '<p class="empty-state">No items yet — add some to start ticking.</p>';
  // On a rest day the list is a wall of things you are not being asked to do,
  // and with several challenges resting at once it buries the ones you are. So
  // it folds away — but only folds: a bonus session on a rest day is still worth
  // ticking, and the toggle keeps that one tap away rather than removing it.
  const live = (ch.items || []).length;   // the view sends active items only
  const resting = ch.due_today === false && !ch.not_started && Boolean(items);
  const list = `<ul class="challenge-list">${items}</ul>`;
  const body = resting
    ? `<details class="challenge-rest" data-challenge="${ch.id}"${restExpanded.has(ch.id) ? " open" : ""}>
         <summary>${live} item${live === 1 ? "" : "s"} · nothing due today</summary>
         ${list}
       </details>`
    : list;
  return `
    <section class="card challenge-card${resting ? " resting" : ""}" data-challenge="${ch.id}">
      <div class="card-head">
        <h2>${escapeHtml(ch.name)}</h2>
        <span class="pill pill-streak">🔥 ${ch.streak}</span>
      </div>
      ${body}
      ${empty}
      <div class="week-dots">${dots}</div>
      ${progress ? `<p class="challenge-progress">${progress}</p>` : ""}
      <div class="card-actions">
        <button type="button" class="link-btn ch-edit" data-challenge="${ch.id}">Edit challenge</button>
        <button type="button" class="link-btn ch-history" data-challenge="${ch.id}">History</button>
        <button type="button" class="link-btn ch-items" data-challenge="${ch.id}">Edit items</button>
      </div>
    </section>`;
}

// Remember an unfolded rest-day list across the re-render that a tick causes.
document.getElementById("challenge-cards").addEventListener("toggle", (e) => {
  const details = e.target.closest("details.challenge-rest");
  if (!details) return;
  const id = Number(details.dataset.challenge);
  if (details.open) restExpanded.add(id);
  else restExpanded.delete(id);
}, true);  // capture: `toggle` does not bubble

document.getElementById("challenge-cards").addEventListener("click", (e) => {
  // ▶ opens the player rather than ticking: same guard shape as the measure
  // select in the library, which sits inside a row that is otherwise tappable.
  const play = e.target.closest(".ci-play");
  if (play) {
    openRoutinePlayer(Number(play.dataset.exercise), Number(play.dataset.item));
    return;
  }
  const el = e.target.closest(".challenge-item");
  if (!el) return;
  const found = findChallengeItem(Number(el.dataset.id));
  if (!found) return;
  const item = found.item;

  // Un-ticking throws work away — the tick, the streak day, and for an
  // exercise the workout it logged with whatever heart rate had been matched
  // to it. Easy to do by accident on a checklist you tap at speed, so ask.
  if (item.done_today && !confirm(undoTickPrompt(item))) return;

  // Optimistic update: flip the check now (and, for exercise items, the Recent
  // workouts card, since ticking one logs a workout) so the UI reacts instantly
  // like a live app. The POST reconciles against the server; on failure we roll
  // the same change back and tell the user.
  const nextDone = !item.done_today;
  const wasComplete = isChallengeComplete(found.challenge);
  const wasAllDone = allDueChallengesComplete(challengeData);
  applyChallengeToggle(item, nextDone, found.challenge);
  // The tick that finishes the day, not merely any tick, and never on the way
  // back down — un-ticking is not an achievement. If that same tick also
  // finishes every other challenge due today, the bigger moment replaces the
  // small one rather than stacking after it.
  if (nextDone && !wasComplete && isChallengeComplete(found.challenge)) {
    if (!wasAllDone && allDueChallengesComplete(challengeData)) {
      celebrateAllDone(challengeData);
    } else {
      celebrateDay(found.challenge);
    }
  }

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
      applyChallengeToggle(item, !nextDone, found.challenge);
      toast("Couldn't update — check your connection.");
    });
});

// Apply a challenge toggle to the local caches and re-render — no network. For
// exercise items this also mirrors the auto-logged workout in the Recent
// workouts card so it tracks the check optimistically.
function applyChallengeToggle(item, done, challenge) {
  item.done_today = done;
  renderChallenge(challengeData);

  if (item.item_type !== "exercise") return;
  const day = challenge.today;
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
  const qs = challengeItemsFor ? `?challenge_id=${challengeItemsFor}` : "";
  try { items = await fetchJSON(`api/challenge/items${qs}`); } catch (e) { return; }
  list.innerHTML = items
    .map((it) => {
      const icon = it.item_type === "supplement" ? "💊" : "🏋️";
      // Editable target (exercise) or dose (supplement), inline.
      const editField =
        it.item_type === "supplement"
          ? `<input type="text" class="ci-edit-dose" data-id="${it.id}" value="${escapeHtml(it.dose || "")}" placeholder="dose">`
          : `<input type="number" class="ci-edit-reps" data-id="${it.id}" data-measure="${it.measure || "reps"}" value="${
              it.measure === "duration"
                ? (it.target_seconds != null ? it.target_seconds : "")
                : (it.target_reps != null ? it.target_reps : "")
            }" placeholder="${it.measure === "duration" ? "seconds" : "reps"}" min="0">`;
      // The inferred date sits in the placeholder, so it is visible without
      // being mistaken for something that was set deliberately.
      const since = `
        <div class="ci-since">
          <label>In this challenge since
            <input type="date" class="ci-joined" data-id="${it.id}"
                   value="${it.joined_on || ""}"
                   placeholder="${it.joined_effective || ""}"
                   title="${it.joined_on ? "Set explicitly" : "Worked out from when it was added"}">
          </label>
          ${it.joined_on ? `<button type="button" class="link-btn ci-joined-clear" data-id="${it.id}">reset</button>` : ""}
        </div>`;
      return `
        <li data-id="${it.id}">
          <div class="ci-row">
            <span class="ci-icon">${icon}</span>
            <span class="ci-name">${escapeHtml(it.name)}</span>
            ${editField}
            ${moveSelectHtml(it)}
            <button type="button" class="list-del ci-del" data-id="${it.id}" aria-label="Remove">✕</button>
          </div>
          ${since}
        </li>`;
    })
    .join("");
}

// Offered only when there is somewhere to move to.
function moveSelectHtml(it) {
  const others = (challengeData || []).filter((c) => c.id !== challengeItemsFor && !c.finished);
  if (!others.length) return "";
  const options = others
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");
  return `<select class="ci-move" data-id="${it.id}" aria-label="Move to another challenge">
      <option value="">Move to…</option>${options}
    </select>`;
}

document.getElementById("challenge-items-list").addEventListener("change", async (e) => {
  const sel = e.target.closest(".ci-move");
  if (!sel || !sel.value) return;
  const name = sel.options[sel.selectedIndex].textContent;
  // Spelled out because it is not a rename: the days already ticked stay with
  // the challenge they were earned in.
  if (!confirm(`Move this item to "${name}"? Days already ticked stay with this challenge.`)) {
    sel.value = "";
    return;
  }
  try {
    await fetchJSON(`api/challenge/items/${sel.dataset.id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ challenge_id: Number(sel.value) }),
    });
    await loadChallenge();
    loadChallengeItems();
    loadChallengeStats();
  } catch (err) { toast(err.message); sel.value = ""; }
});

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
document.getElementById("ci-exercise").addEventListener("change", syncChallengeRepsLabel);

function syncChallengeRepsLabel() {
  const chosen = exerciseById(document.getElementById("ci-exercise").value);
  const timed = chosen && chosen.measure === "duration";
  const field = document.getElementById("ci-reps");
  field.previousSibling.textContent = timed ? "Seconds" : "Reps";
  field.placeholder = timed ? "e.g. 60" : "e.g. 40";
}

// Card actions are delegated: the cards are rebuilt on every render.
document.getElementById("challenge-cards").addEventListener("click", async (e) => {
  const btn = e.target.closest(".ch-edit, .ch-items, .ch-history");
  if (!btn) return;
  const id = Number(btn.dataset.challenge);
  if (btn.classList.contains("ch-edit")) {
    openChallengeEditor(challengeById(id));
  } else if (btn.classList.contains("ch-items")) {
    challengeItemsFor = id;
    const ch = challengeById(id);
    document.getElementById("challenge-items-title").textContent =
      ch ? `Items · ${ch.name}` : "Challenge items";
    openSheet("challenge-items-backdrop");
    await populateChallengeItemForm();
    syncChallengeItemFields();
    syncChallengeRepsLabel();
    loadChallengeItems();
  } else {
    challengeItemsFor = id;
    openChallengeHistory();
  }
});

document.getElementById("challenge-new-btn").addEventListener("click", () => openChallengeEditor(null));

// --- Challenge templates ----------------------------------------------------
// fmtDuration rounds to whole minutes, which reads as "0m" for a 20-second
// cool-down. A template list is mostly short routines, so it needs seconds.
function fmtShortSecs(sec) {
  if (sec == null) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (!m) return `${s}s`;
  return s ? `${m}m ${s}s` : `${m}m`;
}

document.getElementById("challenge-template-btn").addEventListener("click", () => {
  openSheet("challenge-template-backdrop");
  loadChallengeTemplates();
});
document.getElementById("challenge-template-close").addEventListener("click", () => {
  closeSheet("challenge-template-backdrop");
});

async function loadChallengeTemplates() {
  const host = document.getElementById("challenge-template-list");
  host.innerHTML = '<p class="empty-state">Loading…</p>';
  let data;
  try { data = await fetchJSON("api/challenge-templates"); }
  catch (e) { host.innerHTML = '<p class="empty-state">Could not load templates.</p>'; return; }

  const templates = data.templates || [];
  if (!templates.length) {
    host.innerHTML = '<p class="empty-state">No templates yet.</p>';
    return;
  }
  host.innerHTML = templates.map((tpl) => {
    const items = tpl.items.map((it) => `
      <li>${escapeHtml(it.name)}${it.sets ? ` · ${it.sets} sets` : ""}
        <span class="tpl-meta">${it.rounds}× · ${fmtShortSecs(it.seconds)}</span></li>`).join("");
    const tips = (tpl.technique || [])
      .map((tip) => `<li>${escapeHtml(tip)}</li>`).join("");
    return `
      <div class="tpl-card">
        <h3>${escapeHtml(tpl.name)}</h3>
        <p class="tpl-summary">${escapeHtml(tpl.summary)}</p>
        <ul class="tpl-items">${items}</ul>
        <p class="tpl-meta">${tpl.days} days · about ${fmtShortSecs(tpl.total_seconds)} a day</p>
        ${tips ? `<details class="tpl-tips"><summary>Technique</summary><ul>${tips}</ul></details>` : ""}
        <button type="button" class="btn-primary tpl-start" data-template="${escapeHtml(tpl.id)}">Start this challenge</button>
      </div>`;
  }).join("");
}

document.getElementById("challenge-template-list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".tpl-start");
  if (!btn) return;
  btn.disabled = true;
  try {
    await fetchJSON("api/challenges/from-template", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template: btn.dataset.template, start_date: todayISO() }),
    });
    closeSheet("challenge-template-backdrop");
    loadChallenge();
    loadChallengeStats();
    loadExercises();
    toast("Challenge started.");
  } catch (err) {
    toast(err.message);
    btn.disabled = false;
  }
});

function syncScheduleFields() {
  const kind = document.getElementById("challenge-edit-kind").value;
  document.getElementById("challenge-edit-interval-field").hidden = kind !== "interval";
  document.getElementById("challenge-edit-weekdays-field").hidden = kind !== "weekdays";
}
document.getElementById("challenge-edit-kind").addEventListener("change", syncScheduleFields);

document.getElementById("challenge-edit-weekdays").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-day]");
  if (btn) btn.classList.toggle("on");
});

function setScheduleFields(schedule) {
  const kind = (schedule && schedule.kind) || "daily";
  document.getElementById("challenge-edit-kind").value = kind;
  document.getElementById("challenge-edit-interval").value =
    (schedule && schedule.interval) || 2;
  const days = new Set((schedule && schedule.weekdays) || []);
  document.querySelectorAll("#challenge-edit-weekdays [data-day]").forEach((b) => {
    b.classList.toggle("on", days.has(Number(b.dataset.day)));
  });
  syncScheduleFields();
}

function readScheduleFields() {
  const kind = document.getElementById("challenge-edit-kind").value;
  if (kind === "interval") {
    return { schedule_kind: kind, schedule_interval: document.getElementById("challenge-edit-interval").value };
  }
  if (kind === "weekdays") {
    const days = [...document.querySelectorAll("#challenge-edit-weekdays [data-day].on")]
      .map((b) => Number(b.dataset.day));
    return { schedule_kind: kind, schedule_weekdays: days.join(",") };
  }
  return { schedule_kind: "daily" };
}

function openChallengeEditor(ch) {
  delete document.getElementById("challenge-edit-form").dataset.repeatOf;
  setScheduleFields(ch && ch.schedule);
  document.getElementById("challenge-edit-title").textContent = ch ? "Edit challenge" : "New challenge";
  document.getElementById("challenge-edit-id").value = ch ? ch.id : "";
  document.getElementById("challenge-edit-name").value = ch ? ch.name : "";
  document.getElementById("challenge-edit-start").value = ch ? ch.start_date : todayISO();
  document.getElementById("challenge-edit-end").value = ch && ch.end_date ? ch.end_date : "";
  document.getElementById("challenge-edit-delete").hidden = !ch;
  document.getElementById("challenge-edit-result").textContent = "";
  openSheet("challenge-edit-backdrop");
}

document.getElementById("challenge-edit-close").addEventListener("click", () => {
  closeSheet("challenge-edit-backdrop");
});

document.getElementById("challenge-edit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("challenge-edit-id").value;
  const repeatOf = e.target.dataset.repeatOf;
  const result = document.getElementById("challenge-edit-result");
  const payload = {
    name: document.getElementById("challenge-edit-name").value.trim(),
    start_date: document.getElementById("challenge-edit-start").value,
    // Sent even when empty: that is how an end date gets cleared.
    end_date: document.getElementById("challenge-edit-end").value,
    ...readScheduleFields(),
  };
  const url = repeatOf
    ? `api/challenges/${repeatOf}/repeat`
    : id
    ? `api/challenges/${id}`
    : "api/challenges";
  try {
    await fetchJSON(url, {
      method: id && !repeatOf ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    closeSheet("challenge-edit-backdrop");
    loadChallenge();
    loadChallengeStats();
  } catch (err) { result.textContent = err.message; }
});

document.getElementById("challenge-edit-delete").addEventListener("click", async () => {
  const id = document.getElementById("challenge-edit-id").value;
  if (!id) return;
  if (!confirm("Archive this challenge? Its history and statistics are kept.")) return;
  try {
    await fetchJSON(`api/challenges/${id}`, { method: "DELETE" });
    closeSheet("challenge-edit-backdrop");
    loadChallenge();
    loadChallengeStats();
  } catch (err) { toast(err.message); }
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
    payload = { item_type: "supplement", supplement_id, dose: document.getElementById("ci-dose").value,
                challenge_id: challengeItemsFor };
  } else {
    const exercise_id = document.getElementById("ci-exercise").value;
    if (!exercise_id) { toast("Add an exercise in the Library first."); return; }
    const chosen = exerciseById(exercise_id);
    payload = {
      item_type: "exercise",
      exercise_id,
      target_sets: document.getElementById("ci-sets").value,
      challenge_id: challengeItemsFor,
    };
    if (chosen && chosen.measure === "duration") {
      payload.target_seconds = document.getElementById("ci-reps").value;
    } else {
      payload.target_reps = document.getElementById("ci-reps").value;
    }
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
document.getElementById("challenge-items-list").addEventListener("click", async (e) => {
  const clear = e.target.closest(".ci-joined-clear");
  if (!clear) return;
  try {
    await fetchJSON(`api/challenge/items/${clear.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ joined_on: "" }),
    });
    loadChallengeItems();
    loadChallenge();
    loadChallengeStats();
  } catch (err) { toast(err.message); }
});

document.getElementById("challenge-items-list").addEventListener("change", async (e) => {
  const joined = e.target.closest(".ci-joined");
  if (joined) {
    try {
      await fetchJSON(`api/challenge/items/${joined.dataset.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ joined_on: joined.value }),
      });
      loadChallengeItems();
      loadChallenge();
      loadChallengeStats();
    } catch (err) { toast(err.message); }
    return;
  }
  const reps = e.target.closest(".ci-edit-reps");
  const dose = e.target.closest(".ci-edit-dose");
  const field = reps || dose;
  if (!field) return;
  const payload = reps
    ? (field.dataset.measure === "duration"
        ? { target_seconds: field.value }
        : { target_reps: field.value })
    : { dose: field.value };
  try {
    await fetchJSON(`api/challenge/items/${field.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (err) { toast(err.message); }
});

// --- Trends: training sessions ----------------------------------------------

function fmtClock(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

async function loadSessions() {
  const card = document.getElementById("sessions-card");
  let sessions;
  try { sessions = await fetchJSON("api/sessions?days=14"); } catch (e) { return; }
  card.hidden = !sessions.length;
  if (!sessions.length) return;
  document.getElementById("sessions-count").textContent = `${sessions.length} in 14 days`;
  document.getElementById("sessions-list").innerHTML = sessions
    .map((s) => {
      const names = s.exercises.map((e) => e.name).join(", ");
      const bits = [];
      // Zero means the exercises were logged in one go, so how long they
      // actually took is unknown — better omitted than asserted as nothing.
      if (s.minutes) bits.push(`${s.minutes} min`);
      if (s.reps) bits.push(`${s.reps} reps`);
      // Absent until the watch has uploaded, rather than shown as zero.
      if (s.hr_avg != null) bits.push(`♥ ${s.hr_avg} avg${s.hr_max != null ? ` · ${s.hr_max} max` : ""}`);
      return `
        <li>
          <div class="list-main">${escapeHtml(fmtDate(s.day))} · ${escapeHtml(fmtClock(s.start))}–${escapeHtml(fmtClock(s.end))}</div>
          <div class="list-sub">${escapeHtml(names)}</div>
          <div class="list-sub">${escapeHtml(bits.join(" · "))}</div>
        </li>`;
    })
    .join("");
}

// --- Trends: per-challenge statistics ---------------------------------------

const ADHERENCE_DAYS = 30;

document.getElementById("challenge-stats").addEventListener("click", (e) => {
  const btn = e.target.closest(".ch-repeat");
  if (!btn) return;
  openChallengeRepeat(Number(btn.dataset.challenge));
});

// Repeating opens the same editor, pre-filled with a fresh run of the same
// length, so the dates can be adjusted before anything is created.
async function openChallengeRepeat(sourceId) {
  let stats;
  try { stats = await fetchJSON("api/challenges/stats"); } catch (e) { return; }
  const src = stats.find((s) => s.id === sourceId);
  if (!src) return;
  let end = "";
  if (src.end_date) {
    const days = Math.round(
      (new Date(src.end_date) - new Date(src.start_date)) / 86400000
    );
    const to = new Date();
    to.setDate(to.getDate() + days);
    end = to.toISOString().slice(0, 10);
  }
  openChallengeEditor(null);
  setScheduleFields(src.schedule);
  document.getElementById("challenge-edit-title").textContent = `Repeat · ${src.name}`;
  document.getElementById("challenge-edit-name").value = src.name;
  document.getElementById("challenge-edit-start").value = todayISO();
  document.getElementById("challenge-edit-end").value = end;
  document.getElementById("challenge-edit-form").dataset.repeatOf = sourceId;
}

async function loadChallengeStats() {
  const host = document.getElementById("challenge-stats");
  let stats;
  try { stats = await fetchJSON("api/challenges/stats"); } catch (e) { return; }
  if (!stats.length) { host.innerHTML = ""; return; }
  host.innerHTML = stats.map(challengeStatsHtml).join("");
}

// Weigh-ins over exactly the days the adherence bars cover, on the same
// x-positions, so the two can be read against each other. Deliberately not
// presented as cause and effect: a handful of weigh-ins over a few weeks says
// nothing about what moved what.
function weightStripHtml(st, days) {
  const w = (st.weight || {}).points || [];
  if (!days.length || w.length < 2) return "";
  const index = new Map(days.map((d, i) => [d.day, i]));
  const points = w
    .filter((p) => index.has(p.day) && p.weight_kg != null)
    .map((p) => ({ i: index.get(p.day), y: p.weight_kg }));
  if (points.length < 2) return "";

  const W = 300, H = 42, pad = 4;
  const lo = Math.min(...points.map((p) => p.y));
  const hi = Math.max(...points.map((p) => p.y));
  const span = hi - lo || 1;
  // Slot centres, matching how the bars above are laid out.
  const sx = (i) => ((i + 0.5) / days.length) * W;
  const sy = (y) => pad + (1 - (y - lo) / span) * (H - pad * 2);
  const line = points.map((p) => `${sx(p.i).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const dots = points
    .map((p) => `<circle cx="${sx(p.i).toFixed(1)}" cy="${sy(p.y).toFixed(1)}" r="2.5" class="wstrip-dot"/>`)
    .join("");

  const delta = st.weight.delta_kg;
  const deltaText =
    delta == null ? "" : ` · ${delta > 0 ? "+" : ""}${delta} kg over these days`;
  return `
    <figcaption class="wstrip-caption">Weight${escapeHtml(deltaText)}</figcaption>
    <svg class="wstrip" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img"
         aria-label="Weight over the same days">
      <polyline points="${line}" class="wstrip-line"/>${dots}
    </svg>`;
}

const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// Empty for a daily challenge: "every day" is the assumption, not news.
function describeSchedule(schedule) {
  if (!schedule || schedule.kind === "daily") return "";
  if (schedule.kind === "interval") return `every ${schedule.interval} days`;
  return (schedule.weekdays || []).map((d) => WEEKDAY_NAMES[d]).join(", ");
}

function challengeStatsHtml(st) {
  const period = st.end_date
    ? `${fmtDate(st.start_date)} – ${fmtDate(st.end_date)}`
    : `since ${fmtDate(st.start_date)}`;
  const schedule = describeSchedule(st.schedule);
  const pct = st.completion_pct == null ? "—" : `${st.completion_pct}%`;

  // One bar per day: complete, partly done, or missed. A missed day is drawn
  // as an empty track rather than a zero-height bar, so it reads as "nothing
  // done" instead of "no data".
  const days = (st.days || []).slice(-ADHERENCE_DAYS);
  const bars = days
    .map((d) => {
      // A rest day owed nothing, so it is neither a hit nor a miss. Today,
      // still unfinished, owed something and has not failed at it yet — a
      // third thing again, and drawn as one rather than as a miss.
      const rest = d.scheduled === false;
      const state = rest
        ? "rest"
        : d.pending
          ? "pending"
          : d.complete
            ? "full"
            : d.done > 0
              ? "part"
              : "none";
      const height = d.total ? Math.round((d.done / d.total) * 100) : 0;
      const label = rest
        ? "rest day"
        : d.pending
          ? `${d.done}/${d.total} · still open`
          : `${d.done}/${d.total}`;
      // Empty tracks are full height so "nothing done" can't read as "no data";
      // a pending day keeps its real height, so progress so far still shows.
      const empty = state === "none" || rest || (d.pending && !d.done);
      return `<span class="adh-slot" title="${d.day} · ${label}">
        <span class="adh-bar adh-${state}" style="height:${empty ? 100 : Math.max(height, 8)}%"></span>
      </span>`;
    })
    .join("");
  const hasRest = days.some((d) => d.scheduled === false);
  const hasPending = days.some((d) => d.pending);

  const items = (st.items || [])
    .map(
      (it) => `
      <div class="ghist-row">
        <span class="ghist-day">${escapeHtml(it.label)}</span>
        <span class="ghist-bar"><span class="ghist-fill" style="width:${it.rate_pct || 0}%"></span></span>
        <span class="ghist-val">${it.rate_pct == null ? "—" : it.rate_pct + "%"}</span>
      </div>`
    )
    .join("");

  const v = st.volume || {};
  const volumeBits = [];
  if (v.sessions) volumeBits.push(`${v.sessions} logged`);
  if (v.reps) volumeBits.push(`${v.reps} reps`);
  if (v.seconds) volumeBits.push(`${fmtDuration(v.seconds)} held`);
  if (v.hr_avg != null) volumeBits.push(`♥ ${v.hr_avg} avg${v.hr_max != null ? ` · ${v.hr_max} max` : ""}`);

  return `
    <section class="card">
      <div class="card-head">
        <h2>${escapeHtml(st.name)}</h2>
        <span class="pill">${st.finished ? "Finished" : escapeHtml(period)}</span>
      </div>
      ${st.finished ? `<p class="challenge-progress">${escapeHtml(period)}</p>` : ""}

      <div class="mini-stats">
        <div class="mini-stat"><span class="mini-value">${pct}</span><span class="mini-label">Completed</span></div>
        <div class="mini-stat"><span class="mini-value">🔥 ${st.current_streak}</span><span class="mini-label">Streak</span></div>
        <div class="mini-stat"><span class="mini-value">${st.longest_streak}</span><span class="mini-label">Longest</span></div>
      </div>
      <p class="challenge-progress">${st.days_complete} of ${st.days_elapsed} ${schedule ? "due " : ""}days${st.pending_today ? " · today still open" : ""}${schedule ? ` · ${escapeHtml(schedule)}` : ""}</p>

      <figure class="chart-figure">
        <figcaption>Last ${Math.min(ADHERENCE_DAYS, days.length)} days</figcaption>
        <div class="adherence">${bars}</div>
        <div class="adh-legend">
          <span><i class="adh-key adh-full"></i>all done</span>
          <span><i class="adh-key adh-part"></i>partly</span>
          <span><i class="adh-key adh-none"></i>missed</span>
          ${hasPending ? '<span><i class="adh-key adh-pending"></i>today</span>' : ""}
          ${hasRest ? '<span><i class="adh-key adh-rest"></i>rest</span>' : ""}
        </div>
        ${weightStripHtml(st, days)}
      </figure>

      ${items ? `<h3 class="subhead">Per item</h3><div class="ghist">${items}</div>` : ""}
      ${volumeBits.length ? `<p class="challenge-progress">${escapeHtml(volumeBits.join(" · "))}</p>` : ""}
      <div class="card-actions">
        <button type="button" class="link-btn ch-repeat" data-challenge="${st.id}">Repeat this challenge</button>
      </div>
    </section>`;
}

// --- Challenge history (edit past days) ------------------------------------

function openChallengeHistory() {
  openSheet("challenge-history-backdrop");
  // Default to the last two weeks; the user widens "From" to backfill older.
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 13);
  document.getElementById("history-to").value = to.toISOString().slice(0, 10);
  document.getElementById("history-from").value = from.toISOString().slice(0, 10);
  loadChallengeHistory();
}
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
  let qs = from && to ? `from=${from}&to=${to}` : "days=14";
  if (challengeItemsFor) qs += `&challenge_id=${challengeItemsFor}`;
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
  if (wasDone) {
    const item = (challengeHistoryData.items || []).find((it) => it.id === itemId);
    if (item && !confirm(undoTickPrompt(item, day))) return;
  }
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
      const bfCorrected = l.bf_correction_id != null && l.body_fat_pct_raw != null
        ? ` (was ${l.body_fat_pct_raw}%)` : "";
      const bf = l.body_fat_pct != null ? ` · ${l.body_fat_pct}% bf${bfCorrected}` : "";
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

// --- Exercise pictures ------------------------------------------------------

const EXERCISE_IMAGE_PX = 512;

function exerciseImageUrl(id, version) {
  // The version busts the cache when a picture is replaced; without one there
  // is nothing to show.
  return version ? `api/exercises/${id}/image?v=${encodeURIComponent(version)}` : null;
}

function exerciseThumbHtml(id, version, fallback) {
  const url = exerciseImageUrl(id, version);
  return url
    ? `<img class="ex-thumb" src="${url}" alt="" loading="lazy">`
    : `<span class="ex-thumb ex-thumb-none">${fallback}</span>`;
}

// Resized in the browser: the add-on runs on hardware where an image library
// is a liability, and a phone photo is megabytes of detail nobody needs at
// thumbnail size.
function shrinkImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Couldn't read that file."));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error("That doesn't look like an image."));
      img.onload = () => {
        const scale = Math.min(1, EXERCISE_IMAGE_PX / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(
          (blob) => (blob ? resolve(blob) : reject(new Error("Couldn't process that image."))),
          "image/jpeg",
          0.8
        );
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function uploadExerciseImage(exerciseId, file) {
  const blob = await shrinkImage(file);
  const body = new FormData();
  body.append("file", blob, "exercise.jpg");
  const res = await fetch(`api/exercises/${exerciseId}/image`, { method: "POST", body });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Upload failed.");
  }
}

// One picker reused by every row: the row is remembered on it while open.
document.getElementById("exercise-image-input").addEventListener("change", async (e) => {
  const input = e.target;
  const id = input.dataset.exerciseId;
  const file = input.files && input.files[0];
  input.value = "";  // so choosing the same file twice still fires
  if (!id || !file) return;
  try {
    await uploadExerciseImage(id, file);
    loadExercises();
    loadChallenge();
  } catch (err) { toast(err.message); }
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
            <button type="button" class="ex-thumb-btn ex-photo" data-id="${ex.id}"
                    data-has-image="${ex.image_v ? "1" : ""}"
                    aria-label="${ex.image_v ? "Replace or remove picture" : "Add a picture"}">
              ${exerciseThumbHtml(ex.id, ex.image_v, "🏋️")}
            </button>
            <div class="ex-main">
              <div class="ex-name">${escapeHtml(ex.name)}</div>
              ${ex.is_routine
                ? `<div class="ex-cat">${escapeHtml(fmtSeconds(ex.routine_seconds))} · ${ex.routine_rounds} rounds</div>`
                : ex.category ? `<div class="ex-cat">${escapeHtml(ex.category)}</div>` : ""}
            </div>
            <div class="exercise-actions">
              <select class="ex-measure" data-id="${ex.id}" aria-label="How this exercise is counted"
                      ${ex.is_routine ? "disabled" : ""}
                      title="${ex.is_routine
                        ? "A routine is timed — remove its steps to count it in reps"
                        : "Counted in repetitions, or timed"}">
                <option value="reps" ${ex.measure === "duration" ? "" : "selected"}>reps</option>
                <option value="duration" ${ex.measure === "duration" ? "selected" : ""}>time</option>
              </select>
              <button type="button" class="link-btn ex-routine" data-id="${ex.id}"
                      aria-label="Steps" title="Build a timed routine">⏱</button>
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

document.getElementById("exercises-groups").addEventListener("change", async (e) => {
  const select = e.target.closest(".ex-measure");
  if (!select) return;
  try {
    await fetchJSON(`api/exercises/${select.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ measure: select.value }),
    });
    loadExercises();
    loadChallenge();   // labels change with it
  } catch (err) { toast(err.message); }
});

document.getElementById("exercises-groups").addEventListener("click", async (e) => {
  const photo = e.target.closest(".ex-photo");
  if (photo) {
    const input = document.getElementById("exercise-image-input");
    if (photo.dataset.hasImage) {
      // Tapping an existing picture offers to drop it; cancelling replaces it.
      if (confirm("Remove this picture? Cancel to choose a different one instead.")) {
        try {
          await fetchJSON(`api/exercises/${photo.dataset.id}/image`, { method: "DELETE" });
          loadExercises();
          loadChallenge();
        } catch (err) { toast(err.message); }
        return;
      }
    }
    input.dataset.exerciseId = photo.dataset.id;
    input.click();
    return;
  }
  const routine = e.target.closest(".ex-routine");
  if (routine) {
    openRoutineEditor(Number(routine.dataset.id));
    return;
  }
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
  if (e.target.closest(".ex-measure")) return;  // the toggle isn't a row tap
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

function exerciseById(id) {
  return exerciseGroups.flatMap((g) => g.exercises).find((ex) => String(ex.id) === String(id));
}

// A timed exercise is held, not counted: ask for the hold and hide reps, so
// nobody has to encode "60 seconds" as 60 reps the way this used to require.
function syncWorkoutMeasureFields() {
  const chosen = exerciseById(document.getElementById("workout-form-exercise").value);
  const timed = chosen && chosen.measure === "duration";
  const reps = document.getElementById("workout-form-reps");
  reps.closest("label").hidden = !!timed;
  const duration = document.getElementById("workout-form-duration");
  duration.closest("label").classList.toggle("grow", !!timed);
  duration.placeholder = timed ? "60" : "optional";
}
document.getElementById("workout-form-exercise").addEventListener("change", syncWorkoutMeasureFields);

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
  syncWorkoutMeasureFields();
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
      const res = await fetchJSON(`api/workouts/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      // Say so when an edit takes a row out of the challenge's hands, rather
      // than letting the user discover it by un-ticking later and finding the
      // workout still there.
      toast(res && res.detached_from_challenge
        ? "Workout updated — no longer linked to the challenge."
        : "Workout updated.");
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
    // The picker is ordered by how often each exercise has been logged, so the
    // set just logged can change that order. Without this the cached list keeps
    // yesterday's ranking until the page is reloaded.
    exerciseGroups = [];
  } catch (err) { toast(err.message); }
});

// Mirrors _format_seconds in app.py: 60 -> "60s", 90 -> "1m 30s", 240 -> "4m".
function fmtSeconds(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${minutes}m ${rest}s` : `${minutes}m`;
}

function workoutSummary(w) {
  const bits = [];
  if (w.sets != null && w.reps != null) bits.push(`${w.sets}×${w.reps}`);
  else if (w.reps != null) bits.push(`${w.reps} reps`);
  else if (w.sets != null) bits.push(`${w.sets} sets`);
  if (w.weight_kg != null) bits.push(`@ ${w.weight_kg} kg`);
  if (w.duration_sec != null) bits.push(fmtSeconds(w.duration_sec));
  // Heart rate arrives later, once the watch has uploaded, so it is simply
  // absent until then rather than shown as a zero.
  if (w.hr_avg != null) {
    bits.push(`♥ ${w.hr_avg} avg${w.hr_max != null ? ` · ${w.hr_max} max` : ""}`);
  }
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
    // Setting .value in script does not fire `change`, so the reps/duration
    // fields would otherwise keep whatever shape the previously selected
    // exercise needed — a timed workout opening with a reps box, or the other
    // way about.
    syncWorkoutMeasureFields();
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


// --- Routine player ---------------------------------------------------------
// A routine is an exercise made of timed steps, and this walks you through them.
//
// The one thing that has to be right is time. A phone locks its screen and
// throttles timers, so nothing here counts ticks — elapsed is always derived
// from the wall clock, which means the display is correct the instant the tab
// comes back rather than however far behind the interval fell.

let playerRoutine = null;     // the routine being played
let playerItemId = null;      // the challenge item to tick, if it came from one
let playerTimeline = [];      // steps × rounds, flattened, with ms offsets
let playerTotalMs = 0;
let playerTimer = null;
let playerStartedAt = 0;      // Date.now() when Start was pressed
let playerPausedTotal = 0;    // accumulated paused ms
let playerPausedAt = null;    // Date.now() while paused, else null
let playerLastIndex = -1;     // which step was on screen last tick
let playerLastShown = -1;     // which second was on screen last tick
let playerFinished = false;
let playerAudio = null;

// Per-device, not per-account: this phone has a speaker, that laptop does not.
// Same try/catch shape as the tab memory, because ingress can run without
// storage and remembering a preference is not worth failing over.
let playerCues = { visual: true, sound: true, vibrate: true };
const PLAYER_CUES_KEY = "gym.routine.cues";
try {
  const saved = JSON.parse(localStorage.getItem(PLAYER_CUES_KEY) || "null");
  if (saved) playerCues = Object.assign(playerCues, saved);
} catch (e) { /* no storage; the defaults are fine */ }

// iOS Safari has no vibration at all, so the toggle is hidden rather than
// offered as a switch that does nothing.
const CAN_VIBRATE = typeof navigator.vibrate === "function";

const playerEl = document.getElementById("routine-player");

function playerElapsedMs() {
  return (playerPausedAt ?? Date.now()) - playerStartedAt - playerPausedTotal;
}

// Flatten the steps into the run that will actually happen, once, on Start.
// Every step runs including a trailing rest, so the total is rounds × round.
function buildTimeline(routine) {
  const timeline = [];
  let at = 0;
  for (let round = 1; round <= routine.rounds; round += 1) {
    for (const step of routine.steps) {
      const ms = step.seconds * 1000;
      timeline.push({
        round,
        kind: step.kind,
        name: step.kind === "rest" ? "Rest" : (step.name || "Work"),
        seconds: step.seconds,
        imageUrl: step.step_exercise_id && step.image_v
          ? exerciseImageUrl(step.step_exercise_id, step.image_v) : null,
        startMs: at,
        endMs: at + ms,
      });
      at += ms;
    }
  }
  return timeline;
}

function stepIndexAt(ms) {
  for (let i = 0; i < playerTimeline.length; i += 1) {
    if (ms < playerTimeline[i].endMs) return i;
  }
  return playerTimeline.length - 1;
}

// --- cues --------------------------------------------------------------------

function playerBeep(freq, ms, when = 0) {
  if (!playerCues.sound || !playerAudio) return;
  try {
    const start = playerAudio.currentTime + when;
    const osc = playerAudio.createOscillator();
    const gain = playerAudio.createGain();
    // Sine, not square: a square wave through a phone speaker is unpleasant.
    osc.type = "sine";
    osc.frequency.value = freq;
    // Ramped rather than switched, or each tone ends in an audible click.
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.14, start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + ms / 1000);
    osc.connect(gain).connect(playerAudio.destination);
    osc.start(start);
    osc.stop(start + ms / 1000 + 0.02);
  } catch (e) { /* audio is a bonus, never a reason to stop the workout */ }
}

function playerBuzz(pattern) {
  if (!playerCues.vibrate || !CAN_VIBRATE) return;
  try { navigator.vibrate(pattern); } catch (e) { /* ignore */ }
}

function playerFlash() {
  if (!playerCues.visual || reducedMotion.matches) return;
  playerEl.classList.remove("is-flash");
  void playerEl.offsetWidth;   // restart the animation
  playerEl.classList.add("is-flash");
}

function cueStep(step) {
  playerFlash();
  if (step.kind === "rest") {
    playerBeep(440, 0.2);
    playerBuzz([60]);
  } else {
    playerBeep(660, 0.12);
    playerBeep(990, 0.12, 0.13);
    playerBuzz([120]);
  }
}

function cueFinish() {
  playerBeep(660, 0.15);
  playerBeep(880, 0.15, 0.16);
  playerBeep(1180, 0.3, 0.32);
  playerBuzz([60, 60, 60]);
}

function renderCueToggles() {
  const host = document.getElementById("player-cues");
  const cues = [
    ["visual", "Flash"],
    ["sound", "Sound"],
    ...(CAN_VIBRATE ? [["vibrate", "Vibrate"]] : []),
  ];
  host.innerHTML = cues.map(([key, label]) =>
    `<button type="button" class="player-cue" data-cue="${key}"
             aria-pressed="${playerCues[key]}">${label}</button>`).join("");
}

document.getElementById("player-cues").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-cue]");
  if (!btn) return;
  const key = btn.dataset.cue;
  playerCues[key] = !playerCues[key];
  btn.setAttribute("aria-pressed", String(playerCues[key]));
  try { localStorage.setItem(PLAYER_CUES_KEY, JSON.stringify(playerCues)); } catch (err) { /* ignore */ }
});

// --- opening -----------------------------------------------------------------

async function openRoutinePlayer(exerciseId, itemId = null) {
  try {
    playerRoutine = await fetchJSON(`api/exercises/${exerciseId}/routine`);
  } catch (err) { toast(err.message); return; }
  if (!playerRoutine.steps.length) { toast("That routine has no steps yet."); return; }

  playerItemId = itemId;
  playerFinished = false;
  document.getElementById("player-name").textContent = playerRoutine.name;
  document.getElementById("player-total").textContent =
    `${playerRoutine.rounds} round${playerRoutine.rounds === 1 ? "" : "s"} · ` +
    `${fmtSeconds(playerRoutine.total_seconds)}`;
  document.getElementById("player-steps").innerHTML = playerRoutine.steps.map((s) =>
    `<li class="${s.kind}">${escapeHtml(s.kind === "rest" ? "Rest" : (s.name || "Work"))}` +
    ` · ${fmtSeconds(s.seconds)}</li>`).join("");
  renderCueToggles();

  document.getElementById("player-ready").hidden = false;
  document.getElementById("player-run").hidden = true;
  document.getElementById("player-round").hidden = true;
  playerEl.classList.remove("is-work", "is-rest", "is-ending");
  playerEl.hidden = false;
}

function closeRoutinePlayer() {
  stopPlayerTimer();
  releaseWakeLock();
  playerEl.hidden = true;
  playerEl.classList.remove("is-work", "is-rest", "is-ending", "is-flash");
  playerRoutine = null;
  playerItemId = null;
}

// --- the run -----------------------------------------------------------------

document.getElementById("player-start").addEventListener("click", () => {
  // The AudioContext is created here, inside the click, because that press is
  // the user gesture iOS requires to unlock audio — and there is no second
  // chance once the routine is running.
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx && !playerAudio) playerAudio = new Ctx();
    if (playerAudio && playerAudio.state === "suspended") playerAudio.resume();
  } catch (e) { playerAudio = null; }

  playerTimeline = buildTimeline(playerRoutine);
  playerTotalMs = playerTimeline[playerTimeline.length - 1].endMs;
  playerStartedAt = Date.now();
  playerPausedTotal = 0;
  playerPausedAt = null;
  playerLastIndex = -1;
  playerLastShown = -1;
  playerFinished = false;

  document.getElementById("player-ready").hidden = true;
  document.getElementById("player-run").hidden = false;
  document.getElementById("player-round").hidden = false;
  document.getElementById("player-pause").textContent = "Pause";

  cueStep(playerTimeline[0]);
  playerLastIndex = 0;
  tickPlayer();
  playerTimer = setInterval(tickPlayer, 200);
  // After playerTimer is set, never before: requestWakeLock() refuses to take a
  // lock when no routine is running, and that guard read as "not running" when
  // this call sat above the setInterval.
  requestWakeLock();
});

function stopPlayerTimer() {
  if (playerTimer) clearInterval(playerTimer);
  playerTimer = null;
}

function tickPlayer() {
  if (!playerTimeline.length || playerFinished) return;
  const elapsed = playerElapsedMs();

  if (elapsed >= playerTotalMs) { finishRoutine(true); return; }

  const index = stepIndexAt(elapsed);
  const step = playerTimeline[index];
  const remaining = Math.ceil((step.endMs - elapsed) / 1000);

  if (index !== playerLastIndex) {
    // Only cue a transition we actually witnessed. A jump of more than one
    // step, or a boundary already well past, means the tab was asleep — firing
    // the beeps it missed would be four tones at once, which is worse than the
    // silence it replaces.
    const justCrossed = elapsed - step.startMs < 1500;
    if (index === playerLastIndex + 1 && justCrossed) cueStep(step);
    playerLastIndex = index;
    playerLastShown = -1;
    renderPlayerStep(step, index);
  }

  if (remaining !== playerLastShown) {
    playerLastShown = remaining;
    document.getElementById("player-count").textContent = String(remaining);
    playerEl.classList.toggle("is-ending", remaining <= 3);
    // The last three seconds of a step, so you can look up in time.
    if (remaining <= 3 && remaining > 0) playerBeep(880, 0.06);
    // Driven at 1 Hz: .bar-fill eases over 400ms and looks laggy if pushed
    // faster than it can settle.
    document.getElementById("player-overall-bar").style.width =
      `${Math.min(100, (elapsed / playerTotalMs) * 100)}%`;
  }
  document.getElementById("player-step-bar").style.width =
    `${Math.min(100, ((elapsed - step.startMs) / (step.endMs - step.startMs)) * 100)}%`;
}

function renderPlayerStep(step, index) {
  document.getElementById("player-step").textContent = step.name;
  document.getElementById("player-round").textContent =
    `Round ${step.round} of ${playerRoutine.rounds}`;
  const next = playerTimeline[index + 1];
  document.getElementById("player-next").textContent =
    next ? `next · ${next.name} ${fmtSeconds(next.seconds)}` : "last one";

  const thumb = document.getElementById("player-thumb");
  if (step.imageUrl) {
    thumb.src = step.imageUrl;
    thumb.hidden = false;
  } else {
    thumb.hidden = true;
  }
  playerEl.classList.toggle("is-work", step.kind === "work");
  playerEl.classList.toggle("is-rest", step.kind === "rest");
}

document.getElementById("player-pause").addEventListener("click", (e) => {
  if (playerPausedAt === null) {
    playerPausedAt = Date.now();
    e.currentTarget.textContent = "Resume";
  } else {
    playerPausedTotal += Date.now() - playerPausedAt;
    playerPausedAt = null;
    e.currentTarget.textContent = "Pause";
  }
});

document.getElementById("player-skip").addEventListener("click", () => {
  const step = playerTimeline[stepIndexAt(playerElapsedMs())];
  if (!step) return;
  // Skipping moves the clock, not an index, so elapsed stays the single source
  // of truth and the logged duration stays honest about the wall clock.
  playerStartedAt -= step.endMs - playerElapsedMs();
  tickPlayer();
});

document.getElementById("player-stop").addEventListener("click", () => {
  if (!confirm("Stop here? The time you did is logged, but the item is not ticked.")) return;
  finishRoutine(false);
});

document.getElementById("player-close").addEventListener("click", () => {
  if (playerTimer && !playerFinished) {
    if (!confirm("Stop here? The time you did is logged, but the item is not ticked.")) return;
    finishRoutine(false);
    return;
  }
  closeRoutinePlayer();
});

async function finishRoutine(completed) {
  if (playerFinished) return;
  playerFinished = true;
  stopPlayerTimer();
  releaseWakeLock();

  const elapsed = Math.round(Math.min(playerElapsedMs(), playerTotalMs) / 1000);
  const step = playerTimeline[stepIndexAt(playerElapsedMs())];
  const exerciseId = playerRoutine.exercise_id;
  const itemId = playerItemId;

  if (completed) cueFinish();

  const wasAllDone = allDueChallengesComplete(challengeData);
  try {
    const res = await fetchJSON(`api/exercises/${exerciseId}/routine/session`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_id: itemId,
        elapsed_seconds: Math.max(0, elapsed),
        completed,
        rounds_done: step ? step.round : null,
      }),
    });
    closeRoutinePlayer();
    if (completed) {
      await loadChallenge();
      const found = itemId ? findChallengeItem(itemId) : null;
      if (res.done && found && !wasAllDone && allDueChallengesComplete(challengeData)) {
        celebrateAllDone(challengeData);
      } else if (res.done) {
        celebrateDay(found ? found.challenge : { streak: res.streak });
      } else {
        toast("Routine logged.");
      }
    } else {
      toast(`Stopped — ${fmtSeconds(elapsed)} logged.`);
      loadChallenge();
    }
    refreshWorkoutViews();
  } catch (err) {
    // The workout happened whether or not the server heard about it, so say so
    // rather than closing as though it had been recorded.
    toast(`Couldn't save that session: ${err.message}`);
  }
}

// Wall-clock elapsed means the display is simply correct when the tab returns;
// there is no catch-up to run. Never auto-pause on hidden — a timed routine has
// to keep running while you look away from the phone.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && playerTimer) tickPlayer();
  if (!document.hidden) requestWakeLock();
});

// --- keeping the screen on ---------------------------------------------------
// Needs a secure context, which ingress inherits from Home Assistant: over
// HTTPS this keeps the screen alive through a plank, over plain HTTP it simply
// does not exist and nothing breaks. Deliberately not the looping-muted-video
// hack, which burns battery and stops working every other browser release.
let wakeLockHandle = null;

async function requestWakeLock() {
  if (!("wakeLock" in navigator) || wakeLockHandle || !playerTimer) return;
  try {
    wakeLockHandle = await navigator.wakeLock.request("screen");
    wakeLockHandle.addEventListener("release", () => { wakeLockHandle = null; });
  } catch (e) { wakeLockHandle = null; }
}

function releaseWakeLock() {
  if (!wakeLockHandle) return;
  try { wakeLockHandle.release(); } catch (e) { /* ignore */ }
  wakeLockHandle = null;
}


// --- Routine editor ---------------------------------------------------------
// The steps an exercise is made of. Unlike the challenge-items sheet, which
// saves on every change, this edits a local draft and PUTs once: a reorder is N
// changes, and a half-applied reorder is a mess to recover from.

let routineDraft = null;      // {exercise_id, name, rounds, steps: [...]}

async function openRoutineEditor(exerciseId) {
  let routine;
  try { routine = await fetchJSON(`api/exercises/${exerciseId}/routine`); }
  catch (err) { toast(err.message); return; }

  routineDraft = {
    exercise_id: exerciseId,
    name: routine.name,
    rounds: routine.rounds || 1,
    steps: routine.steps.map((s) => ({
      id: s.id, kind: s.kind, seconds: s.seconds,
      step_exercise_id: s.step_exercise_id,
      label: s.step_exercise_id ? null : s.name,
      name: s.name,
    })),
  };
  document.getElementById("routine-edit-title").textContent = `Routine · ${routine.name}`;
  document.getElementById("routine-rounds").value = routineDraft.rounds;
  populateRoutineExercisePicker(exerciseId);
  renderRoutineDraft();
  openSheet("routine-edit-backdrop");
}

function populateRoutineExercisePicker(exerciseId) {
  // Routines are excluded, and so is this exercise: a routine inside a routine
  // is an unbounded timeline, and the server refuses it anyway.
  const select = document.getElementById("routine-step-exercise");
  select.innerHTML = exerciseGroups.map((g) => {
    const options = g.exercises
      .filter((ex) => !ex.is_routine && ex.id !== exerciseId)
      .map((ex) => `<option value="${ex.id}">${escapeHtml(ex.name)}</option>`)
      .join("");
    return options ? `<optgroup label="${escapeHtml(g.equipment)}">${options}</optgroup>` : "";
  }).join("");
}

function renderRoutineDraft() {
  const host = document.getElementById("routine-steps-list");
  const steps = routineDraft.steps;
  host.innerHTML = steps.length
    ? steps.map((s, i) => `
        <li>
          <span class="ci-label">${escapeHtml(s.kind === "rest" ? "Rest" : (s.name || s.label || "Work"))}
            · ${escapeHtml(fmtSeconds(s.seconds))}</span>
          <button type="button" class="link-btn rs-up" data-idx="${i}"
                  aria-label="Move up" ${i === 0 ? "disabled" : ""}>▲</button>
          <button type="button" class="link-btn rs-down" data-idx="${i}"
                  aria-label="Move down" ${i === steps.length - 1 ? "disabled" : ""}>▼</button>
          <button type="button" class="list-del rs-del" data-idx="${i}" aria-label="Remove">✕</button>
        </li>`).join("")
    : '<li class="empty-state">No steps yet — add one below.</li>';

  const round = steps.reduce((sum, s) => sum + s.seconds, 0);
  const rounds = routineDraft.rounds;
  document.getElementById("routine-summary").textContent = steps.length
    ? `${rounds} round${rounds === 1 ? "" : "s"} · ${fmtSeconds(round)} each · ${fmtSeconds(round * rounds)} total`
    : "";
}

document.getElementById("routine-rounds").addEventListener("input", (e) => {
  if (!routineDraft) return;
  routineDraft.rounds = Math.min(99, Math.max(1, parseInt(e.target.value, 10) || 1));
  renderRoutineDraft();
});

// The step type decides which field is asked for; a rest needs neither.
document.getElementById("routine-step-kind").addEventListener("change", (e) => {
  const kind = e.target.value;
  document.getElementById("routine-step-exercise-label").hidden = kind !== "exercise";
  document.getElementById("routine-step-text-label").hidden = kind !== "text";
  document.getElementById("routine-step-seconds").value = kind === "rest" ? 15 : 30;
});

document.getElementById("routine-step-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!routineDraft) return;
  const kind = document.getElementById("routine-step-kind").value;
  const seconds = parseInt(document.getElementById("routine-step-seconds").value, 10);
  if (!seconds || seconds < 1) { toast("How many seconds?"); return; }

  if (kind === "rest") {
    routineDraft.steps.push({ kind: "rest", seconds, step_exercise_id: null, label: null });
  } else if (kind === "exercise") {
    const id = parseInt(document.getElementById("routine-step-exercise").value, 10);
    if (!id) { toast("Pick an exercise."); return; }
    const chosen = exerciseById(id);
    routineDraft.steps.push({
      kind: "work", seconds, step_exercise_id: id, label: null,
      name: chosen ? chosen.name : "Work",
    });
  } else {
    const label = document.getElementById("routine-step-text").value.trim();
    if (!label) { toast("Give the step a name."); return; }
    routineDraft.steps.push({ kind: "work", seconds, step_exercise_id: null, label, name: label });
    document.getElementById("routine-step-text").value = "";
  }
  renderRoutineDraft();
});

// Arrows rather than drag-and-drop: HTML5 dragging does not work on touch, and
// a pointer-events drag inside a scrolling sheet is a lot of fragile code with
// no test harness here to catch it breaking.
document.getElementById("routine-steps-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-idx]");
  if (!btn || !routineDraft) return;
  const idx = Number(btn.dataset.idx);
  const steps = routineDraft.steps;
  if (btn.classList.contains("rs-del")) steps.splice(idx, 1);
  else if (btn.classList.contains("rs-up") && idx > 0) {
    [steps[idx - 1], steps[idx]] = [steps[idx], steps[idx - 1]];
  } else if (btn.classList.contains("rs-down") && idx < steps.length - 1) {
    [steps[idx], steps[idx + 1]] = [steps[idx + 1], steps[idx]];
  }
  renderRoutineDraft();
});

// The canonical interval workout, and the quickest way to see what a routine is.
document.getElementById("routine-tabata").addEventListener("click", () => {
  if (!routineDraft) return;
  routineDraft.rounds = 8;
  routineDraft.steps = [
    { kind: "work", seconds: 20, step_exercise_id: null, label: "Work", name: "Work" },
    { kind: "rest", seconds: 10, step_exercise_id: null, label: null },
  ];
  document.getElementById("routine-rounds").value = 8;
  renderRoutineDraft();
});

async function saveRoutineDraft() {
  return fetchJSON(`api/exercises/${routineDraft.exercise_id}/routine`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rounds: routineDraft.rounds,
      steps: routineDraft.steps.map((s) => ({
        id: s.id, kind: s.kind, seconds: s.seconds,
        step_exercise_id: s.step_exercise_id, label: s.label,
      })),
    }),
  });
}

document.getElementById("routine-save").addEventListener("click", async () => {
  if (!routineDraft) return;
  try {
    await saveRoutineDraft();
    closeSheet("routine-edit-backdrop");
    toast(routineDraft.steps.length ? "Routine saved." : "Routine cleared.");
    loadExercises();
    loadChallenge();
  } catch (err) { toast(err.message); }
});

// Saved first, because the player reads the routine back from the server —
// previewing something that only exists in the browser would run the old one.
document.getElementById("routine-preview").addEventListener("click", async () => {
  if (!routineDraft || !routineDraft.steps.length) { toast("Add a step first."); return; }
  try {
    await saveRoutineDraft();
    closeSheet("routine-edit-backdrop");
    loadExercises();
    openRoutinePlayer(routineDraft.exercise_id, null);
  } catch (err) { toast(err.message); }
});

document.getElementById("routine-edit-close").addEventListener("click", () => {
  closeSheet("routine-edit-backdrop");
});

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
    <div class="reminder-line">${badge(data.weighin.enabled)}<span>Weigh-in · ${
      data.weighin.weekday === "daily" ? "every day" : escapeHtml(data.weighin.weekday)
    } ${escapeHtml(data.weighin.time)}</span></div>
    <div class="reminder-line">${badge(data.quote.enabled)}<span>Daily stoic quote · ${escapeHtml(data.quote.time)}</span></div>`;

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
    window._profile = await fetchJSON("api/profile");
  } catch (e) { window._profile = {}; }
  const profile = window._profile;
  document.getElementById("profile-form-sex").value = profile.sex || "";
  document.getElementById("profile-form-age").value = profile.age != null ? profile.age : "";
  document.getElementById("profile-form-activity").value = profile.activity_level != null ? profile.activity_level : "";
  document.getElementById("profile-form-since").value = profile.activity_level_set_at || "";

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

document.getElementById("profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = document.getElementById("profile-form-result");
  const payload = {
    sex: document.getElementById("profile-form-sex").value,
    age: document.getElementById("profile-form-age").value,
    activity_level: document.getElementById("profile-form-activity").value,
    activity_level_set_at: document.getElementById("profile-form-since").value,
  };
  try {
    const res = await fetchJSON("api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    window._profile = res.profile;
    result.textContent = "Profile saved.";
  } catch (err) { result.textContent = err.message; }
});

document.getElementById("profile-calibration-btn").addEventListener("click", () => {
  openSheet("calibration-backdrop");
  loadCalibration();
});
document.getElementById("calibration-close-btn").addEventListener("click", () => {
  closeSheet("calibration-backdrop");
});

// --- Body-fat calibration ---------------------------------------------------

function renderCalibSummary(summary) {
  const el = document.getElementById("calib-offset-summary");
  if (!summary.count) {
    el.textContent = "No readings yet.";
  } else {
    const sign = summary.offset_pct > 0 ? "+" : "";
    el.textContent = `Offset: ${sign}${summary.offset_pct}pp (from ${summary.count} reading${summary.count === 1 ? "" : "s"}, spread ${summary.spread_pct}pp)`;
  }
  document.getElementById("calib-apply-offset").placeholder = summary.offset_pct != null ? summary.offset_pct : "";
  const cutoff = document.getElementById("calib-apply-cutoff");
  if (!cutoff.value && window._profile && window._profile.activity_level_set_at) {
    cutoff.value = window._profile.activity_level_set_at;
  }
  updateCalibPreview();
}

function renderCalibHistory(readings) {
  const list = document.getElementById("calib-history");
  list.innerHTML = readings
    .slice()
    .reverse()
    .map((r) => {
      const note = r.notes ? ` · ${escapeHtml(r.notes)}` : "";
      return `
        <li data-id="${r.id}">
          <div class="list-main">
            <div class="list-title">${r.old_bf_pct}% → ${r.new_bf_pct}%</div>
            <div class="list-sub">${escapeHtml(fmtDate(r.recorded_at))}${note}</div>
          </div>
          <button type="button" class="list-del calib-del" data-id="${r.id}" aria-label="Delete">✕</button>
        </li>`;
    })
    .join("");
}

async function loadCalibration() {
  if (!window._weightLogs) await loadWeightHistory();
  let summary;
  try { summary = await fetchJSON("api/bf-calibration"); } catch (e) { return; }
  renderCalibSummary(summary);
  renderCalibHistory(summary.readings || []);
  loadCalibEvents();
}

function updateCalibPreview() {
  const cutoff = document.getElementById("calib-apply-cutoff").value;
  const preview = document.getElementById("calib-apply-preview");
  if (!cutoff) { preview.textContent = ""; return; }
  const cutoffTs = `${cutoff}T00:00:00`;
  const logs = window._weightLogs || [];
  const n = logs.filter((l) => l.ts < cutoffTs && l.body_fat_pct != null && l.bf_correction_id == null).length;
  preview.textContent = `${n} historical entr${n === 1 ? "y" : "ies"} will be corrected.`;
}
document.getElementById("calib-apply-cutoff").addEventListener("change", updateCalibPreview);

document.getElementById("calib-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = {
    old_bf_pct: document.getElementById("calib-form-old").value,
    new_bf_pct: document.getElementById("calib-form-new").value,
    notes: document.getElementById("calib-form-notes").value,
  };
  try {
    const summary = await fetchJSON("api/bf-calibration", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderCalibSummary(summary);
    renderCalibHistory(summary.readings || []);
    e.target.reset();
  } catch (err) { toast(err.message); }
});

document.getElementById("calib-history").addEventListener("click", async (e) => {
  const del = e.target.closest(".calib-del");
  if (!del) return;
  if (!confirm("Delete this calibration reading?")) return;
  try {
    const summary = await fetchJSON(`api/bf-calibration/${del.dataset.id}`, { method: "DELETE" });
    renderCalibSummary(summary);
    renderCalibHistory(summary.readings || []);
  } catch (err) { toast(err.message); }
});

document.getElementById("calib-apply-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const result = document.getElementById("calib-apply-result");
  const cutoff = document.getElementById("calib-apply-cutoff").value;
  const offsetOverride = document.getElementById("calib-apply-offset").value;
  const preview = document.getElementById("calib-apply-preview").textContent;
  if (!confirm(`Apply this correction? ${preview}`)) return;
  const payload = { cutoff_date: cutoff };
  if (offsetOverride !== "") payload.offset_pct = offsetOverride;
  try {
    const res = await fetchJSON("api/bf-correction/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    result.textContent = `Corrected ${res.rows_affected} entr${res.rows_affected === 1 ? "y" : "ies"}.`;
    await loadWeightHistory();
    updateCalibPreview();
    loadCalibEvents();
  } catch (err) { result.textContent = err.message; }
});

function renderCalibEvents(events) {
  const list = document.getElementById("calib-events");
  list.innerHTML = events
    .map((ev) => {
      const sign = ev.offset_pct > 0 ? "+" : "";
      const status = ev.reverted_at
        ? `<span class="list-sub">Reverted</span>`
        : `<button type="button" class="link-btn calib-revert" data-id="${ev.id}">Revert</button>`;
      return `
        <li data-id="${ev.id}">
          <div class="list-main">
            <div class="list-title">${sign}${ev.offset_pct}pp on ${ev.rows_affected} entr${ev.rows_affected === 1 ? "y" : "ies"}</div>
            <div class="list-sub">before ${escapeHtml(fmtDate(ev.cutoff_ts))} · applied ${escapeHtml(fmtDate(ev.applied_at))}</div>
          </div>
          ${status}
        </li>`;
    })
    .join("");
}

async function loadCalibEvents() {
  let events;
  try { events = await fetchJSON("api/bf-correction/events"); } catch (e) { return; }
  renderCalibEvents(events);
}

document.getElementById("calib-events").addEventListener("click", async (e) => {
  const revert = e.target.closest(".calib-revert");
  if (!revert) return;
  if (!confirm("Revert this correction? Affected entries go back to their original values.")) return;
  try {
    await fetchJSON(`api/bf-correction/${revert.dataset.id}/revert`, { method: "POST" });
    await loadWeightHistory();
    updateCalibPreview();
    loadCalibEvents();
  } catch (err) { toast(err.message); }
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
  resting: { key: "resting_hr", max: 80, fmt: (v) => `${v} bpm` },
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
      // A day that arrived without this metric is not the same as a day that
      // never arrived — only the latter can be blamed on the watch.
      const why = row
        ? "—"
        : uploadedThrough && day > uploadedThrough
        ? "not synced"
        : "no data";
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
    if (data.backfill_days) {
      parts.push(`<div class="reminder-line"><span class="muted">Filling gaps up to ${data.backfill_days} days back</span></div>`);
    }
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

document.getElementById("garmin-diagnose-btn").addEventListener("click", async () => {
  const panel = document.getElementById("garmin-diagnostics");
  const out = document.getElementById("garmin-diagnose-output");
  panel.hidden = false;
  out.textContent = "Asking Garmin…";
  // Yesterday: today's data may not have finished arriving.
  const day = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  try {
    const data = await fetchJSON(`api/garmin/diagnose?day=${day}`);
    out.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    out.textContent = `Failed: ${err.message}`;
  }
});

document.getElementById("garmin-diagnose-copy").addEventListener("click", async () => {
  const pre = document.getElementById("garmin-diagnose-output");
  // navigator.clipboard only exists in a secure context, and ingress over a
  // LAN address isn't one — so select the text and try the old command, which
  // does work there. Worst case the text is selected and ready to copy by hand.
  const range = document.createRange();
  range.selectNodeContents(pre);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(pre.textContent);
    } else if (!document.execCommand("copy")) {
      throw new Error("copy rejected");
    }
    toast("Diagnostics copied.");
  } catch (e) {
    toast("Selected it — press copy on your keyboard.");
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
loadChallengeStats();
loadSessions();
loadRecentWorkouts();
loadGarminCard();
pingStatus();
