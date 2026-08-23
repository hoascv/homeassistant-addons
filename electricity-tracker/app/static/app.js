"use strict";

const state = {
  today: [],
  tomorrow: [],
  currentDay: "today",
  consumptionView: "hourly",
};

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function fetchJSON(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function relTime(isoUtc) {
  if (!isoUtc) return null;
  const then = new Date(isoUtc).getTime();
  if (Number.isNaN(then)) return null;
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)} min ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} h ago`;
  return `${Math.floor(diffSec / 86400)} d ago`;
}

function hm(isoLocal) {
  return isoLocal.slice(11, 16);
}

function fmtKwh(value) {
  return value == null ? "–" : `${value.toFixed(value < 10 ? 2 : 1)}`;
}

function fmtKr(value) {
  return value == null ? "–" : value.toFixed(2);
}

// --- "Price now" card ---

function priceTier(value, rows) {
  if (!rows || !rows.length) return "normal";
  const values = rows.map((r) => r.total_dkk_kwh);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  if (value <= min + range / 3) return "cheap";
  if (value >= max - range / 3) return "expensive";
  return "normal";
}

function renderNow(data) {
  const valueEl = document.getElementById("price-now-value");
  const updatedEl = document.getElementById("now-updated");
  const breakdownEl = document.getElementById("price-breakdown");
  const cheapestEl = document.getElementById("cheapest-today");
  const priciestEl = document.getElementById("priciest-today");

  const now = data.current_price;
  if (!now) {
    valueEl.textContent = "–";
    valueEl.className = "price-now-value";
    breakdownEl.textContent = "";
    updatedEl.textContent = "no data";
  } else {
    valueEl.textContent = now.total_dkk_kwh.toFixed(2);
    valueEl.className = `price-now-value price-${priceTier(now.total_dkk_kwh, data.today)}`;
    breakdownEl.innerHTML = [
      `Spot ${now.spot_dkk_kwh.toFixed(2)}`,
      `Grid ${now.grid_tariff_dkk_kwh.toFixed(2)} (${now.grid_tariff_band})`,
      `Transmission ${now.transmission_tariff_dkk_kwh.toFixed(2)}`,
      `Tax ${now.electricity_tax_dkk_kwh.toFixed(2)}`,
      `+${Math.round(now.vat_rate * 100)}% VAT`,
    ].map((s) => `<span>${escapeHtml(s)}</span>`).join("");
    updatedEl.textContent = hm(now.time_dk);
  }

  cheapestEl.textContent = data.cheapest_hour_today
    ? `${hm(data.cheapest_hour_today.time_dk)} · ${data.cheapest_hour_today.total_dkk_kwh.toFixed(2)} kr`
    : "–";
  priciestEl.textContent = data.priciest_hour_today
    ? `${hm(data.priciest_hour_today.time_dk)} · ${data.priciest_hour_today.total_dkk_kwh.toFixed(2)} kr`
    : "–";

  document.getElementById("price-area-pill").textContent = data.price_area || "–";

  renderPowerNow(data.saveeye, now);
}

function renderPowerNow(saveeye, currentPrice) {
  const row = document.getElementById("power-now-row");
  const valueEl = document.getElementById("power-now-value");
  const rateEl = document.getElementById("power-now-rate");

  const watts = saveeye && saveeye.payload ? saveeye.payload.instant_power_w : null;
  if (watts == null) {
    row.hidden = true;
    return;
  }
  row.hidden = false;
  const kw = watts / 1000;
  valueEl.textContent = `${kw.toFixed(2)} kW now`;
  document.getElementById("power-now-dot").style.background = saveeye.connected ? "var(--success)" : "var(--text-muted)";
  if (currentPrice) {
    const rate = kw * currentPrice.total_dkk_kwh;
    rateEl.textContent = `≈ ${rate.toFixed(2)} kr/h at this rate`;
  } else {
    rateEl.textContent = "";
  }
}

// --- Shared chart primitives: a smooth line + soft gradient-wash area ---

// Catmull-Rom through every point, converted to cubic Bezier segments (the
// standard 1/6-tension form) — a natural curve with no manual tangent math
// at each call site.
function smoothLinePath(points) {
  if (points.length === 1) {
    // A single point still needs a valid path (a bare "M" is legal SVG and
    // draws nothing) — the area path built on top of it degrades to a
    // harmless zero-width sliver rather than malformed "L" with no "M".
    return `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`;
  }
  if (points.length === 0) return "";
  if (points.length === 2) {
    return `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)} L${points[1][0].toFixed(2)},${points[1][1].toFixed(2)}`;
  }
  let d = `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] || points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(2)},${c1y.toFixed(2)} ${c2x.toFixed(2)},${c2y.toFixed(2)} ${p2[0].toFixed(2)},${p2[1].toFixed(2)}`;
  }
  return d;
}

// One shared renderer for the price curve and both consumption views — same
// shape (a time series), same treatment (smooth line, soft area wash,
// per-point hover, sparse axis labels), differing only in baseline,
// tooltip text, and which optional markers apply.
//
// `opts.series` is one entry per line to draw, each with its own `valueOf`;
// a series that returns null for a row simply has no point there, so two
// series covering different stretches of the same x axis (measured hours vs
// live estimate) share one set of scales and axis labels. Series are drawn
// in order, so put the primary one first — extreme dots, the "now" marker
// and the hover targets all anchor to it.
function renderSmoothChart(host, rows, opts) {
  const series = opts.series;
  const hasAnyValue = rows && rows.some((r) => series.some((s) => s.valueOf(r) != null));
  if (!hasAnyValue) {
    host.innerHTML = `<p class="empty-state">${opts.emptyText}</p>`;
    return null;
  }

  const W = 600, H = 160, padTop = 14, padBottom = 20, padX = 3;
  const values = [];
  rows.forEach((r) => series.forEach((s) => {
    const v = s.valueOf(r);
    if (v != null) values.push(v);
  }));
  const min = opts.baseline === "zero" ? 0 : Math.min(...values);
  const max = Math.max(...values, min + 0.01);
  const range = max - min;
  const innerW = W - padX * 2;
  const stepX = rows.length > 1 ? innerW / (rows.length - 1) : 0;
  const baselineY = H - padBottom;

  const pointAt = (s, r, i) => {
    const v = s.valueOf(r);
    if (v == null) return null;
    return [padX + i * stepX, padTop + (1 - (v - min) / range) * (H - padTop - padBottom)];
  };
  const seriesPoints = series.map((s) => rows.map((r, i) => pointAt(s, r, i)));

  let defs = "";
  let areas = "";
  let lines = "";
  series.forEach((s, si) => {
    // A series can be interrupted (Saveeye was offline for an hour); each
    // uninterrupted run is its own path so the gap stays a gap instead of
    // being bridged by a straight line that implies data we don't have.
    for (const run of contiguousRuns(seriesPoints[si])) {
      const linePath = smoothLinePath(run);
      if (s.area) {
        const first = run[0], last = run[run.length - 1];
        areas +=
          `<path class="chart-area" fill="url(#${s.gradientId})" ` +
          `d="${linePath} L${last[0].toFixed(2)},${baselineY} L${first[0].toFixed(2)},${baselineY} Z"/>`;
      }
      lines += `<path class="chart-line ${s.lineClass || ""}" d="${linePath}"/>`;
    }
    if (s.area) {
      defs +=
        `<linearGradient id="${s.gradientId}" x1="0" y1="0" x2="0" y2="1">` +
        `<stop offset="0%" class="chart-area-stop-top ${s.stopClass || ""}"/>` +
        `<stop offset="100%" class="chart-area-stop-bottom ${s.stopClass || ""}"/>` +
        `</linearGradient>`;
    }
  });

  const primary = seriesPoints[0];
  let extras = "";
  if (opts.nowIndex != null && opts.nowIndex >= 0 && primary[opts.nowIndex]) {
    const [nx, ny] = primary[opts.nowIndex];
    extras +=
      `<line class="chart-now-line" x1="${nx.toFixed(2)}" y1="${padTop}" x2="${nx.toFixed(2)}" y2="${baselineY}"/>` +
      `<circle class="chart-now-dot" cx="${nx.toFixed(2)}" cy="${ny.toFixed(2)}" r="3"/>`;
  }

  if (opts.extremeDots) {
    const valueOf = series[0].valueOf;
    let minIdx = -1, maxIdx = -1;
    rows.forEach((r, i) => {
      if (valueOf(r) == null) return;
      if (minIdx < 0 || valueOf(r) < valueOf(rows[minIdx])) minIdx = i;
      if (maxIdx < 0 || valueOf(r) > valueOf(rows[maxIdx])) maxIdx = i;
    });
    if (minIdx >= 0) {
      extras +=
        `<circle class="chart-dot chart-dot-cheap" cx="${primary[minIdx][0].toFixed(2)}" cy="${primary[minIdx][1].toFixed(2)}" r="2.6"/>` +
        `<circle class="chart-dot chart-dot-expensive" cx="${primary[maxIdx][0].toFixed(2)}" cy="${primary[maxIdx][1].toFixed(2)}" r="2.6"/>`;
    }
  }

  // One hover target per x position, on whichever series actually has a point
  // there — the tooltip text covers every series at once anyway.
  const hitTargets = rows.map((r, i) => {
    const p = seriesPoints.map((sp) => sp[i]).find(Boolean);
    if (!p) return "";
    return (
      `<circle class="chart-hit" cx="${p[0].toFixed(2)}" cy="${p[1].toFixed(2)}" r="7">` +
      `<title>${escapeHtml(opts.tooltipOf(r))}</title></circle>`
    );
  }).join("");

  const labels = rows.map((r, i) => {
    const text = opts.axisLabelOf(r, i);
    return text ? `<text class="chart-axis-label" x="${(padX + i * stepX).toFixed(2)}" y="${H - 4}">${text}</text>` : "";
  }).join("");

  host.innerHTML = (
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${escapeHtml(opts.ariaLabel)}">` +
    `<defs>${defs}</defs>` + areas + lines + extras + hitTargets + labels +
    `</svg>`
  );
  return { seriesPoints };
}

function contiguousRuns(points) {
  const runs = [];
  let run = [];
  for (const p of points) {
    if (p) {
      run.push(p);
    } else if (run.length) {
      runs.push(run);
      run = [];
    }
  }
  if (run.length) runs.push(run);
  return runs;
}

// --- Price curve chart ---

function renderPriceChart(rows, highlightKey) {
  const host = document.getElementById("price-chart");
  const empty = document.getElementById("price-chart-empty");
  if (!rows || !rows.length) {
    host.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const nowIndex = highlightKey ? rows.findIndex((r) => r.time_dk === highlightKey) : -1;
  renderSmoothChart(host, rows, {
    series: [{ valueOf: (r) => r.total_dkk_kwh, area: true, gradientId: "price-area-gradient" }],
    tooltipOf: (r) => `${hm(r.time_dk)} — ${r.total_dkk_kwh.toFixed(2)} DKK/kWh`,
    axisLabelOf: (r) => {
      const hourStr = r.time_dk.slice(11, 13);
      const minStr = r.time_dk.slice(14, 16);
      return minStr === "00" && Number(hourStr) % 3 === 0 ? hourStr : "";
    },
    emptyText: "No price data yet.",
    ariaLabel: "Electricity price today",
    baseline: "min",
    nowIndex,
    extremeDots: true,
  });
}

function currentDayRows() {
  return state.currentDay === "tomorrow" ? state.tomorrow : state.today;
}

function refreshChart(nowLocalIso) {
  const rows = currentDayRows();
  // Last (most recent) row at or before now — not the first: `rows` is
  // ascending and every earlier row also satisfies "<= now", so a plain
  // .find() always matched index 0 regardless of the actual time.
  let highlight = null;
  if (state.currentDay === "today" && nowLocalIso) {
    const nowKey = nowLocalIso.slice(0, 16) + ":00";
    for (let i = rows.length - 1; i >= 0; i--) {
      if (rows[i].time_dk <= nowKey) {
        highlight = rows[i].time_dk;
        break;
      }
    }
  }
  renderPriceChart(rows, highlight);
  document.getElementById("curve-day-note").textContent =
    rows.length ? `(${rows.length / 4}h)` : "";
}

// --- Consumption summary tiles ---

function renderConsumptionSummary(consumption, configured) {
  const empty = document.getElementById("consumption-empty");
  const tiles = document.getElementById("consumption-tiles");
  const chartCard = document.getElementById("consumption-chart-card");

  if (!configured || !consumption) {
    empty.hidden = false;
    tiles.style.opacity = "0.4";
    chartCard.hidden = true;
    return;
  }
  empty.hidden = true;
  tiles.style.opacity = "1";
  chartCard.hidden = false;

  document.getElementById("c-today-kwh").textContent = fmtKwh(consumption.today_kwh);
  document.getElementById("c-yesterday-kwh").textContent = fmtKwh(consumption.yesterday_kwh);
  document.getElementById("c-week-kwh").textContent = fmtKwh(consumption.week_kwh);
  document.getElementById("c-month-kwh").textContent = fmtKwh(consumption.month_kwh);
  document.getElementById("c-today-cost").textContent = fmtKr(consumption.today_cost_dkk);
  document.getElementById("c-yesterday-cost").textContent = fmtKr(consumption.yesterday_cost_dkk);
  document.getElementById("c-week-cost").textContent = fmtKr(consumption.week_cost_dkk);
  document.getElementById("c-month-cost").textContent = fmtKr(consumption.month_cost_dkk);
}

// --- EV charging (Easee) ---

function renderEasee(easee) {
  const card = document.getElementById("easee-card");
  if (!easee) {
    card.hidden = true;
    return;
  }
  card.hidden = false;

  const session = easee.session;
  const empty = document.getElementById("easee-empty");
  if (!session) {
    empty.hidden = false;
    document.getElementById("easee-status-pill").textContent = "no data yet";
    document.getElementById("easee-power").textContent = "–";
    document.getElementById("easee-energy").textContent = "–";
    document.getElementById("easee-cost").textContent = "–";
    document.getElementById("easee-started").textContent = "";
    return;
  }
  empty.hidden = true;

  // A status is only as current as the poll behind it. Easee is polled on the
  // background tick, and a failed sync writes no row at all — so a stale
  // reading has to say so rather than sit there looking live.
  const age = relTime(session.measured_at);
  const stale = isStale(session.measured_at);
  const pill = document.getElementById("easee-status-pill");
  pill.textContent = stale ? `${session.status || "–"} · ${age}` : session.status || "–";
  pill.classList.toggle("pill-stale", stale);

  document.getElementById("easee-power").textContent =
    session.total_power_w != null ? `${(session.total_power_w / 1000).toFixed(2)} kW` : "–";
  document.getElementById("easee-energy").textContent =
    session.session_energy_kwh != null ? `${session.session_energy_kwh.toFixed(2)} kWh` : "–";
  document.getElementById("easee-cost").textContent =
    session.session_cost_dkk != null ? `${session.session_cost_dkk.toFixed(2)} kr` : "–";

  const started = relTime(session.session_started_at);
  const ended = relTime(session.session_ended_at);
  document.getElementById("easee-started").textContent = !started
    ? ""
    : ended
    // The car has been unplugged since, so this is a past charge, not a
    // running one whose start keeps receding into the distance.
    ? `Last session ${started} → ended ${ended}`
    : session.session_start_observed
    ? `Session started ${started}`
    : `Watching since ${started}`;

  const notes = [];
  // Easee reports CHARGING through a pause, so a derived PAUSED needs to say
  // why — otherwise it just looks like the add-on disagreeing with the app.
  if (session.status === "PAUSED") {
    notes.push(session.reason
      ? `Plugged in but not drawing — ${session.reason}.`
      : "Plugged in but not drawing: the charger reports a session with no power.");
  }
  // The cost and the energy can describe different amounts: energy is Easee's
  // own session counter, cost is only what this add-on was awake to price.
  // Saying so is the difference between a cheap-looking charge and a wrong one.
  if (session.cost_is_partial) {
    notes.push(
      `Cost covers ${session.cost_covers_kwh.toFixed(2)} of ${session.session_energy_kwh.toFixed(2)} kWh — ` +
      "the rest was charged before this add-on was watching.");
  }
  document.getElementById("easee-note").textContent = notes.join(" ");
}

// Two background ticks without a reading means a sync is failing or the add-on
// was asleep; either way the number on screen is no longer a live one.
const EASEE_STALE_AFTER_MS = 2 * 300 * 1000;

function isStale(isoUtc) {
  if (!isoUtc) return true;
  const then = new Date(isoUtc).getTime();
  if (Number.isNaN(then)) return true;
  return Date.now() - then > EASEE_STALE_AFTER_MS;
}

// --- Consumption chart (measured vs live estimate) ---

// Two comparable series over the same hours: what the meter actually
// reported through Eloverblik, and what Saveeye's own counter says. They
// cover different stretches — Eloverblik runs 1-3 days behind, Saveeye only
// goes back as far as this add-on has been collecting — which is exactly
// what makes seeing both worthwhile.
const CONSUMPTION_SERIES = [
  { name: "measured", valueOf: (r) => r.measured_kwh, area: true, gradientId: "consumption-measured-gradient" },
  { name: "saveeye", valueOf: (r) => r.saveeye_kwh, lineClass: "chart-line-saveeye" },
];

function fmtSeriesPair(measured, saveeye, suffix) {
  const parts = [];
  if (measured != null) parts.push(`meter ${measured.toFixed(2)} kWh${suffix ? suffix(true) : ""}`);
  if (saveeye != null) parts.push(`Saveeye ${saveeye.toFixed(2)} kWh${suffix ? suffix(false) : ""}`);
  return parts.length ? parts.join(" · ") : "no reading";
}

function aggregateDaily(rows) {
  const map = new Map();
  for (const r of rows) {
    const day = r.time_dk.slice(0, 10);
    const entry = map.get(day) || {
      day, kwh: 0, cost: 0, costKnown: true,
      measured_kwh: null, measuredHours: 0,
      saveeye_kwh: null, saveeyeHours: 0,
    };
    entry.kwh += r.kwh;
    if (r.cost_dkk != null) entry.cost += r.cost_dkk;
    else entry.costKnown = false;
    if (r.measured_kwh != null) {
      entry.measured_kwh = (entry.measured_kwh || 0) + r.measured_kwh;
      entry.measuredHours += 1;
    }
    if (r.saveeye_kwh != null) {
      entry.saveeye_kwh = (entry.saveeye_kwh || 0) + r.saveeye_kwh;
      entry.saveeyeHours += 1;
    }
    map.set(day, entry);
  }
  return [...map.values()].sort((a, b) => a.day.localeCompare(b.day));
}

function renderHourlyChart(rows) {
  const host = document.getElementById("consumption-chart");
  renderSmoothChart(host, rows, {
    series: CONSUMPTION_SERIES,
    tooltipOf: (r) => {
      const costStr = r.cost_dkk != null ? `${r.cost_dkk.toFixed(2)} kr` : "cost n/a";
      const pair = fmtSeriesPair(r.measured_kwh, r.saveeye_kwh,
        (isMeasured) => (!isMeasured && r.source === "saveeye_partial" ? " so far" : ""));
      return `${hm(r.time_dk)} — ${pair}, ${costStr}`;
    },
    axisLabelOf: (r) => (Number(r.time_dk.slice(11, 13)) % 3 === 0 ? r.time_dk.slice(11, 13) : ""),
    emptyText: "No consumption data yet today.",
    ariaLabel: "Hourly consumption today, measured and live estimate",
    baseline: "zero",
  });
}

function renderDailyChart(rows) {
  const host = document.getElementById("consumption-chart");
  const daily = aggregateDaily(rows);
  const step = Math.max(1, Math.ceil(daily.length / 10));
  renderSmoothChart(host, daily, {
    series: CONSUMPTION_SERIES,
    tooltipOf: (d) => {
      const costStr = d.costKnown ? `${d.cost.toFixed(2)} kr` : "cost n/a";
      // A day either source only partly covers is flagged with its hour count —
      // otherwise a half-reported day reads as a genuine drop in consumption.
      const hours = (n) => (n > 0 && n < 24 ? ` (${n}/24 h)` : "");
      const pair = fmtSeriesPair(d.measured_kwh, d.saveeye_kwh,
        (isMeasured) => hours(isMeasured ? d.measuredHours : d.saveeyeHours));
      return `${d.day} — ${pair}, ${costStr}`;
    },
    axisLabelOf: (d, i) => (i % step === 0 ? d.day.slice(5) : ""),
    emptyText: "No consumption data yet.",
    ariaLabel: "Daily consumption, measured and live estimate",
    baseline: "zero",
  });
}

function renderConsumptionChart(rows) {
  const legend = document.getElementById("consumption-chart-legend");
  const hasMeasured = rows.some((r) => r.measured_kwh != null);
  const hasSaveeye = rows.some((r) => r.saveeye_kwh != null);
  document.getElementById("legend-measured").hidden = !hasMeasured;
  document.getElementById("legend-saveeye").hidden = !hasSaveeye;
  legend.hidden = !(hasMeasured || hasSaveeye);
  if (state.consumptionView === "hourly") {
    renderHourlyChart(rows);
  } else {
    renderDailyChart(rows);
  }
}

async function loadConsumptionChart() {
  const days = state.consumptionView === "hourly" ? 1 : state.consumptionView;
  try {
    const rows = await fetchJSON(`api/consumption?days=${days}`);
    renderConsumptionChart(rows);
  } catch (err) {
    document.getElementById("consumption-chart").innerHTML =
      `<p class="empty-state">Could not load consumption: ${escapeHtml(String(err))}</p>`;
  }
}

// --- Main summary load ---

async function loadSummary() {
  let data;
  try {
    data = await fetchJSON("api/summary");
  } catch (err) {
    document.getElementById("now-updated").textContent = "error";
    return;
  }
  state.today = data.today || [];
  state.tomorrow = data.tomorrow || [];
  document.getElementById("curve-tomorrow-btn").disabled = state.tomorrow.length === 0;

  renderNow(data);
  refreshChart(data.now_local);
  renderConsumptionSummary(data.consumption, data.eloverblik_configured);
  renderEasee(data.easee);

  const priceSync = relTime(data.last_price_sync);
  const consumptionSync = relTime(data.last_consumption_sync);
  document.getElementById("last-price-sync").textContent =
    priceSync ? `Prices synced ${priceSync}` : "Prices not yet synced";
  document.getElementById("last-consumption-sync").textContent =
    data.eloverblik_configured ? (consumptionSync ? `Consumption synced ${consumptionSync}` : "Consumption not yet synced") : "";
}

// --- Settings sheet ---

async function refreshSaveeyeStatus() {
  const out = document.getElementById("saveeye-status");
  try {
    const data = await fetchJSON("api/saveeye/now");
    if (!data.enabled) {
      out.textContent = "saveeye_enabled is off.";
      return;
    }
    if (!data.payload) {
      out.textContent = `Enabled, waiting for the first message${data.detail ? ` (${data.detail})` : ""}...`;
      return;
    }
    const w = data.payload.instant_power_w;
    const cum = data.payload.cumulative_wh;
    let storedCount = "?";
    try {
      const stats = await fetchJSON("api/stats");
      storedCount = stats.counts && stats.counts.saveeye_samples != null ? stats.counts.saveeye_samples : "?";
    } catch (_) {
      // best-effort — the status line still works without it
    }
    out.textContent =
      `${data.connected ? "Connected" : "Disconnected"} — device ${data.payload.device_serial}\n` +
      `Instant power: ${w != null ? `${w} W` : "n/a"}\n` +
      `Cumulative energy: ${cum != null ? `${cum} Wh` : "MISSING — this device's telemetry has no energy counter, only power"}\n` +
      `Samples stored (needed for hourly kWh): ${storedCount}\n` +
      `Last message: ${relTime(data.received_at) || data.received_at}`;
  } catch (err) {
    out.textContent = `Could not reach /api/saveeye/now: ${err}`;
  }
}

let _saveeyeStatusInterval = null;

function wireSettingsSheet() {
  const backdrop = document.getElementById("settings-backdrop");
  document.getElementById("settings-open-btn").addEventListener("click", () => {
    backdrop.classList.add("open");
    refreshSaveeyeStatus();
    _saveeyeStatusInterval = setInterval(refreshSaveeyeStatus, 10000);
  });
  const closeSettings = () => {
    backdrop.classList.remove("open");
    clearInterval(_saveeyeStatusInterval);
  };
  document.getElementById("settings-close-btn").addEventListener("click", closeSettings);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeSettings();
  });

  document.getElementById("eloverblik-test-btn").addEventListener("click", async () => {
    const btn = document.getElementById("eloverblik-test-btn");
    const out = document.getElementById("eloverblik-test-result");
    btn.disabled = true;
    btn.textContent = "Testing…";
    out.innerHTML = "";
    try {
      const res = await fetch("api/eloverblik/diagnose");
      const data = await res.json();
      if (!res.ok || !data.ok) {
        out.innerHTML = `<div class="diag-output">Error: ${escapeHtml(data.error || res.statusText)}</div>`;
      } else if (!data.metering_points || !data.metering_points.length) {
        out.innerHTML =
          '<div class="diag-output">Connected, but no metering points came back. Make sure the ' +
          "refresh token belongs to the account the meter is registered to.</div>";
      } else {
        const lines = data.metering_points.map((mp) => JSON.stringify(mp, null, 2)).join("\n\n");
        out.innerHTML =
          `<p class="muted">Found ${data.metering_points.length} metering point(s). Copy the id ` +
          `into <code>eloverblik_metering_point</code>:</p><div class="diag-output">${escapeHtml(lines)}</div>`;
      }
    } catch (err) {
      out.innerHTML = `<div class="diag-output">Request failed: ${escapeHtml(String(err))}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Test Eloverblik connection";
    }
  });

  document.getElementById("easee-test-btn").addEventListener("click", async () => {
    const btn = document.getElementById("easee-test-btn");
    const out = document.getElementById("easee-test-result");
    btn.disabled = true;
    btn.textContent = "Testing…";
    out.innerHTML = "";
    try {
      const res = await fetch("api/easee/diagnose");
      const data = await res.json();
      if (!res.ok || !data.ok) {
        out.innerHTML = `<div class="diag-output">Error: ${escapeHtml(data.error || res.statusText)}</div>`;
      } else if (!data.chargers || !data.chargers.length) {
        out.innerHTML = '<div class="diag-output">Connected, but no chargers came back on this account.</div>';
      } else {
        const lines = data.chargers.map((c) => `${c.id} — ${c.name}`).join("\n");
        out.innerHTML =
          `<p class="muted">Found ${data.chargers.length} charger(s). Leave <code>easee_charger_id</code> ` +
          `empty to use the first one, or copy an id in to pick a specific one:</p>` +
          `<div class="diag-output">${escapeHtml(lines)}</div>`;
      }
    } catch (err) {
      out.innerHTML = `<div class="diag-output">Request failed: ${escapeHtml(String(err))}</div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Test Easee connection";
    }
  });
}

function wireChartToggles() {
  document.getElementById("curve-day-toggle").addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn || btn.disabled) return;
    document.querySelectorAll("#curve-day-toggle .seg-btn").forEach((b) => b.classList.remove("seg-on"));
    btn.classList.add("seg-on");
    state.currentDay = btn.dataset.day;
    refreshChart(new Date().toISOString());
  });

  document.getElementById("consumption-range-toggle").addEventListener("click", (e) => {
    const btn = e.target.closest(".seg-btn");
    if (!btn) return;
    document.querySelectorAll("#consumption-range-toggle .seg-btn").forEach((b) => b.classList.remove("seg-on"));
    btn.classList.add("seg-on");
    state.consumptionView = btn.dataset.view === "hourly" ? "hourly" : Number(btn.dataset.view);
    loadConsumptionChart();
  });
}

function init() {
  wireSettingsSheet();
  wireChartToggles();
  loadSummary();
  loadConsumptionChart();
  setInterval(loadSummary, 60000);
  setInterval(loadConsumptionChart, 300000);
}

document.addEventListener("DOMContentLoaded", init);
