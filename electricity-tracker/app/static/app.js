"use strict";

const state = {
  today: [],
  tomorrow: [],
  currentDay: "today",
  consumptionDays: 7,
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

// --- Price curve chart ---

function tierClass(value, min, max) {
  const range = max - min || 1;
  if (value <= min + range / 3) return "chart-bar-cheap";
  if (value >= max - range / 3) return "chart-bar-normal";
  return "chart-bar-expensive";
}

function renderPriceChart(rows, highlightKey) {
  const host = document.getElementById("price-chart");
  const empty = document.getElementById("price-chart-empty");
  if (!rows || !rows.length) {
    host.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  const W = 600, H = 160, padBottom = 16, padTop = 4;
  const values = rows.map((r) => r.total_dkk_kwh);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, min + 0.01);
  const range = max - min;
  const barW = W / rows.length;

  const bars = rows.map((r, i) => {
    const h = ((r.total_dkk_kwh - min) / range) * (H - padTop - padBottom);
    const x = i * barW;
    const y = H - padBottom - h;
    const cls = tierClass(r.total_dkk_kwh, min, max);
    const isNow = highlightKey && r.time_dk === highlightKey;
    const label = `${hm(r.time_dk)} — ${r.total_dkk_kwh.toFixed(2)} DKK/kWh`;
    return (
      `<rect class="chart-bar ${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" ` +
      `width="${Math.max(1, barW - 0.6).toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" ` +
      `opacity="${isNow ? 1 : 0.8}">` +
      `<title>${escapeHtml(label)}</title></rect>`
    );
  }).join("");

  const labels = rows.map((r, i) => {
    const hourStr = r.time_dk.slice(11, 13);
    const minStr = r.time_dk.slice(14, 16);
    if (minStr !== "00" || Number(hourStr) % 3 !== 0) return "";
    const x = i * barW;
    return `<text class="chart-axis-label" x="${x.toFixed(1)}" y="${H - 4}">${hourStr}</text>`;
  }).join("");

  host.innerHTML = (
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Electricity price today">` +
    bars + labels + `</svg>`
  );
}

function currentDayRows() {
  return state.currentDay === "tomorrow" ? state.tomorrow : state.today;
}

function refreshChart(nowLocalIso) {
  const rows = currentDayRows();
  const highlight = state.currentDay === "today" && nowLocalIso
    ? rows.find((r) => r.time_dk <= nowLocalIso.slice(0, 16) + ":00")?.time_dk
    : null;
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

  document.getElementById("easee-status-pill").textContent = session.status || "–";
  document.getElementById("easee-power").textContent =
    session.total_power_w != null ? `${(session.total_power_w / 1000).toFixed(2)} kW` : "–";
  document.getElementById("easee-energy").textContent =
    session.session_energy_kwh != null ? `${session.session_energy_kwh.toFixed(2)} kWh` : "–";
  document.getElementById("easee-cost").textContent =
    session.session_cost_dkk != null ? `${session.session_cost_dkk.toFixed(2)} kr` : "–";
  const started = relTime(session.session_started_at);
  document.getElementById("easee-started").textContent = started ? `Session started ${started}` : "";
}

// --- Consumption chart (daily totals) ---

function aggregateDaily(rows) {
  const map = new Map();
  for (const r of rows) {
    const day = r.time_dk.slice(0, 10);
    const entry = map.get(day) || { day, kwh: 0, cost: 0, costKnown: true, hasEstimate: false };
    entry.kwh += r.kwh;
    if (r.cost_dkk != null) entry.cost += r.cost_dkk;
    else entry.costKnown = false;
    if (r.source === "saveeye_estimate") entry.hasEstimate = true;
    map.set(day, entry);
  }
  return [...map.values()].sort((a, b) => a.day.localeCompare(b.day));
}

function renderConsumptionChart(rows) {
  const host = document.getElementById("consumption-chart");
  const daily = aggregateDaily(rows);
  document.getElementById("consumption-chart-legend").hidden = !rows.some((r) => r.source === "saveeye_estimate");
  if (!daily.length) {
    host.innerHTML = '<p class="empty-state">No consumption data yet.</p>';
    return;
  }

  const W = 600, H = 160, padBottom = 16, padTop = 4;
  const max = Math.max(...daily.map((d) => d.kwh), 0.1);
  const barW = W / daily.length;

  const bars = daily.map((d, i) => {
    const h = (d.kwh / max) * (H - padTop - padBottom);
    const x = i * barW;
    const y = H - padBottom - h;
    const costStr = d.costKnown ? `${d.cost.toFixed(2)} kr` : "cost n/a";
    const estimateNote = d.hasEstimate ? " (partly a live estimate)" : "";
    const label = `${d.day} — ${d.kwh.toFixed(2)} kWh, ${costStr}${estimateNote}`;
    const estimateClass = d.hasEstimate ? " chart-bar-estimate" : "";
    return (
      `<rect class="chart-bar chart-bar-normal${estimateClass}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" ` +
      `width="${Math.max(1, barW - 1).toFixed(1)}" height="${Math.max(1, h).toFixed(1)}" opacity="0.85">` +
      `<title>${escapeHtml(label)}</title></rect>`
    );
  }).join("");

  const step = Math.ceil(daily.length / 10);
  const labels = daily.map((d, i) => {
    if (i % step !== 0) return "";
    const x = i * barW;
    return `<text class="chart-axis-label" x="${x.toFixed(1)}" y="${H - 4}">${d.day.slice(5)}</text>`;
  }).join("");

  host.innerHTML = (
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Daily consumption">` +
    bars + labels + `</svg>`
  );
}

async function loadConsumptionChart() {
  try {
    const rows = await fetchJSON(`api/consumption?days=${state.consumptionDays}`);
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
    } else if (!data.payload) {
      out.textContent = `Enabled, waiting for the first message${data.detail ? ` (${data.detail})` : ""}...`;
    } else {
      const w = data.payload.instant_power_w;
      out.textContent =
        `${data.connected ? "Connected" : "Disconnected"} — device ${data.payload.device_serial}\n` +
        `Instant power: ${w != null ? `${w} W` : "n/a"}\n` +
        `Last message: ${relTime(data.received_at) || data.received_at}`;
    }
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
    state.consumptionDays = Number(btn.dataset.days);
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
