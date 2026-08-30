"use strict";

// The session token lives in sessionStorage and rides on a header, never in a
// cookie: ingress puts every add-on on one origin, and a cookie would be
// offered to the neighbours. sessionStorage also means closing the tab ends
// the session, which for a journal is the right default rather than a
// limitation.
const TOKEN_KEY = "journal-session";

const MOODS = [
  { value: 1, emoji: "😞", label: "Rough" },
  { value: 2, emoji: "😕", label: "Off" },
  { value: 3, emoji: "😐", label: "Fine" },
  { value: 4, emoji: "🙂", label: "Good" },
  { value: 5, emoji: "😄", label: "Great" },
];

const CALENDAR_WEEKS = 12;
const AUTOSAVE_MS = 1500;

const state = {
  today: null,
  day: null,
  sections: [],      // the current template
  entry: null,       // what is stored for the day on screen
  goals: [],
  mood: null,
  tags: [],
  dirty: false,
  saveTimer: null,
  stats: null,
};

function el(id) {
  return document.getElementById(id);
}

function escapeHtml(str) {
  return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// --- Talking to the add-on ---

function token() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch (_) {
    // Private-mode browsers can throw on storage. The journal still works;
    // it just asks for the password again on every load.
    return "";
  }
}

function setToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch (_) { /* see token() */ }
}

async function fetchJSON(path, options) {
  const opts = { ...(options || {}) };
  opts.headers = { ...(opts.headers || {}) };
  if (token()) opts.headers["X-Journal-Session"] = token();
  const res = await fetch(path, opts);
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    // A proxy error page still has to surface as an error, not a parse crash.
  }
  if (res.status === 401 && body && body.error === "locked") {
    // The key went out of memory — idle timeout, a restart, or someone hit
    // the padlock on another device. Back to the lock screen, mid-flight.
    setToken("");
    showLock();
    throw new Error("locked");
  }
  if (!res.ok) throw new Error((body && body.error) || `${path}: HTTP ${res.status}`);
  return body;
}

function sendJSON(method, path, payload) {
  return fetchJSON(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// --- Dates ---

function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function parseDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

function shiftDay(iso, days) {
  const d = parseDay(iso);
  d.setDate(d.getDate() + days);
  return isoDay(d);
}

function longDate(iso) {
  return parseDay(iso).toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long", year: "numeric",
  });
}

function shortDate(iso) {
  return parseDay(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

// --- The lock ---

function showLock(message) {
  el("journal").hidden = true;
  el("lock-card").hidden = false;
  el("streak-pill").hidden = true;
  el("lock-btn").hidden = true;
  el("settings-open-btn").hidden = true;
  const error = el("lock-error");
  error.textContent = message || "";
  error.hidden = !message;
}

function showError(message) {
  const error = el("lock-error");
  error.textContent = message;
  error.hidden = false;
}

async function boot() {
  const info = await fetchJSON("api/state");
  state.today = info.today;
  state.stats = info.stats;
  el("setup-password").value = "";
  el("unlock-password").value = "";

  if (!info.vault_exists) {
    showLock();
    el("lock-setup").hidden = false;
    el("lock-unlock").hidden = true;
    el("setup-password").focus();
    return;
  }
  el("lock-setup").hidden = true;
  el("lock-unlock").hidden = false;
  el("locked-stats").textContent = describeLocked(info.stats);

  if (!info.unlocked) {
    showLock();
    el("unlock-password").focus();
    return;
  }
  await openJournal(state.day || info.today);
}

function describeLocked(stats) {
  // Shown while locked, so it says only what the database knows while locked:
  // how many days, how long a run, when the last one was. Never a word of it.
  if (!stats || !stats.entries) return "Nothing written yet.";
  const bits = [`${stats.entries} ${stats.entries === 1 ? "day" : "days"} written`];
  if (stats.streak) bits.push(`${stats.streak}-day streak`);
  if (stats.last_entry_on) bits.push(`last on ${shortDate(stats.last_entry_on)}`);
  return bits.join(" · ");
}

async function unlock(password) {
  const res = await sendJSON("POST", "api/unlock", { password });
  setToken(res.token);
  await openJournal(state.today);
}

async function openJournal(day) {
  el("lock-card").hidden = true;
  el("journal").hidden = false;
  el("lock-btn").hidden = false;
  el("settings-open-btn").hidden = false;
  await loadDay(day || state.today);
  await refreshCalendar();
}

async function lockNow() {
  await flushSave();
  await sendJSON("POST", "api/lock", {});
  setToken("");
  closeAllSheets();
  await boot();
}

// --- A day ---

async function loadDay(day) {
  await flushSave();
  state.day = day;
  const data = await fetchJSON(`api/entry?day=${encodeURIComponent(day)}`);
  state.entry = data.entry;
  state.sections = data.sections;
  state.goals = data.goals;
  state.mood = data.entry ? data.entry.mood : null;
  state.tags = data.entry ? data.entry.tags.slice() : [];

  el("day-input").value = day;
  el("entry-heading").textContent = day === state.today ? "Today" : longDate(day);
  renderDayLabel(day, data.neighbours);
  renderSections();
  renderMood();
  renderTags();
  renderGoals();
  renderOnThisDay(data.on_this_day);
  setSaveState(data.entry ? `Saved ${timeOf(data.entry.updated_at)}` : "Not written yet");
  state.dirty = false;
  await refreshStats();
}

function timeOf(isoTimestamp) {
  if (!isoTimestamp) return "";
  const at = new Date(isoTimestamp);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
}

function renderDayLabel(day, neighbours) {
  const parts = [escapeHtml(longDate(day))];
  // Jumping to the nearest day that was actually written beats clicking the
  // arrow through a fortnight of nothing.
  if (neighbours && neighbours.previous_written && neighbours.previous_written !== shiftDay(day, -1)) {
    parts.push(`<button type="button" class="recall-open" data-goto="${neighbours.previous_written}">‹ ${escapeHtml(shortDate(neighbours.previous_written))}</button>`);
  }
  if (neighbours && neighbours.next_written && neighbours.next_written !== shiftDay(day, 1)) {
    parts.push(`<button type="button" class="recall-open" data-goto="${neighbours.next_written}">${escapeHtml(shortDate(neighbours.next_written))} ›</button>`);
  }
  el("day-label").innerHTML = parts.join(" · ");
}

function renderSections() {
  // The template, plus any section this entry was written under that the
  // template has since lost. A heading you removed in March should not
  // swallow what you wrote under it in February.
  const inTemplate = new Set(state.sections.map((s) => s.key));
  const stored = state.entry ? state.entry.sections : [];
  const extra = stored.filter((s) => !inTemplate.has(s.key));
  const rows = [
    ...state.sections.map((s) => ({ ...s, text: (stored.find((x) => x.key === s.key) || {}).text || "" })),
    ...extra.map((s) => ({ ...s, hint: "No longer one of your sections — kept because you wrote it.", retired: true })),
  ];

  el("sections-host").innerHTML = rows.map((section) => `
    <div class="entry-section">
      <div class="entry-section-head">
        <label for="sec-${escapeHtml(section.key)}">${escapeHtml(section.title)}</label>
        ${section.retired ? '<span class="pill">retired</span>' : ""}
      </div>
      ${section.hint ? `<div class="entry-section-hint">${escapeHtml(section.hint)}</div>` : ""}
      <textarea id="sec-${escapeHtml(section.key)}" data-section-key="${escapeHtml(section.key)}"
        data-section-title="${escapeHtml(section.title)}" rows="3">${escapeHtml(section.text || "")}</textarea>
    </div>`).join("");

  el("sections-host").querySelectorAll("textarea").forEach((box) => {
    box.addEventListener("input", markDirty);
    box.addEventListener("blur", flushSave);
  });
}

function renderMood() {
  el("mood-row").innerHTML = MOODS.map((mood) => `
    <button type="button" class="mood-btn mood-${mood.value}" data-mood="${mood.value}"
      aria-pressed="${state.mood === mood.value}" title="${mood.label}" aria-label="${mood.label}">
      ${mood.emoji}
    </button>`).join("");
  el("mood-row").querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const picked = Number(btn.dataset.mood);
      // Clicking the chosen mood again clears it: a day you would rather not
      // score should not be stuck with the first thing you tapped.
      state.mood = state.mood === picked ? null : picked;
      renderMood();
      markDirty();
    });
  });
}

function renderTags() {
  el("tags-input").value = state.tags.join(", ");
  el("tags-chips").innerHTML = state.tags.map((tag) => `<span class="chip">#${escapeHtml(tag)}</span>`).join("");
}

function parseTags(raw) {
  const seen = new Set();
  return String(raw || "")
    .split(/[,\n]/)
    .map((t) => t.trim().replace(/^#/, "").toLowerCase())
    .filter((t) => t && !seen.has(t) && seen.add(t));
}

function renderGoals() {
  const host = el("goals-host");
  const checkins = state.entry ? state.entry.goals : [];
  const byId = new Map(checkins.map((c) => [c.id, c]));
  // Active goals, plus any goal this day already has a note against — closing
  // a goal must not hide what you wrote about it while it was open.
  const shown = state.goals.filter((goal) => goal.status === "active" || byId.has(goal.id));

  el("goals-empty").hidden = shown.length > 0;
  host.innerHTML = shown.map((goal) => {
    const checkin = byId.get(goal.id) || { note: "", moved: false };
    const quiet = goal.needs_attention && goal.status === "active";
    return `
      <div class="goal-row ${goal.status !== "active" ? "goal-done" : ""}">
        <div class="goal-head">
          <span class="goal-title">${escapeHtml(goal.title)}</span>
          ${quiet ? `<span class="pill pill-warn">${goal.days_since_checkin == null ? "never" : goal.days_since_checkin + "d"}</span>` : ""}
        </div>
        <div class="goal-sub">${escapeHtml(goalSubtitle(goal))}</div>
        <div class="goal-checkin">
          <input type="text" id="goal-note-${escapeHtml(goal.id)}" data-goal-id="${escapeHtml(goal.id)}"
            placeholder="What moved today?" value="${escapeHtml(checkin.note || "")}">
          <label class="goal-moved">
            <input type="checkbox" data-goal-moved="${escapeHtml(goal.id)}" ${checkin.moved ? "checked" : ""}>
            moved
          </label>
        </div>
      </div>`;
  }).join("");

  host.querySelectorAll("input[type=text]").forEach((box) => {
    box.addEventListener("input", markDirty);
    box.addEventListener("blur", flushSave);
  });
  host.querySelectorAll("input[type=checkbox]").forEach((box) => {
    box.addEventListener("change", markDirty);
  });
}

function goalSubtitle(goal) {
  const bits = [];
  if (goal.target_date) {
    const left = goal.days_left;
    bits.push(left == null ? goal.target_date
      : left < 0 ? `target was ${shortDate(goal.target_date)}`
      : left === 0 ? "target is today"
      : `${left} days to ${shortDate(goal.target_date)}`);
  }
  bits.push(goal.checkins ? `${goal.checkins} check-in${goal.checkins === 1 ? "" : "s"}` : "no check-ins yet");
  if (goal.last_checkin) bits.push(`last ${shortDate(goal.last_checkin.day)}`);
  return bits.join(" · ");
}

function renderOnThisDay(entries) {
  const card = el("onthisday-card");
  if (!entries || !entries.length) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  el("onthisday-host").innerHTML = entries.map((entry) => {
    const first = entry.sections[0];
    return `
      <div class="recall">
        <div class="recall-head">
          <span class="recall-when">${entry.years_ago} year${entry.years_ago === 1 ? "" : "s"} ago</span>
          <button type="button" class="recall-open" data-goto="${entry.day}">open ${escapeHtml(shortDate(entry.day))}</button>
        </div>
        ${first ? `<div class="recall-section">${escapeHtml(first.title)}</div>
        <p class="recall-body">${escapeHtml(trim(first.text, 240))}</p>` : ""}
      </div>`;
  }).join("");
}

function trim(text, max) {
  const clean = String(text || "").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

// --- Saving ---

function markDirty() {
  state.dirty = true;
  setSaveState("Unsaved…");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(flushSave, AUTOSAVE_MS);
}

function collectEntry() {
  const sections = [...el("sections-host").querySelectorAll("textarea")].map((box) => ({
    key: box.dataset.sectionKey,
    title: box.dataset.sectionTitle,
    text: box.value,
  }));
  const goals = [...el("goals-host").querySelectorAll("input[type=text]")].map((box) => ({
    id: box.dataset.goalId,
    note: box.value,
    moved: el("goals-host").querySelector(`input[data-goal-moved="${box.dataset.goalId}"]`).checked,
  }));
  return { day: state.day, sections, mood: state.mood, tags: parseTags(el("tags-input").value), goals };
}

async function flushSave() {
  clearTimeout(state.saveTimer);
  if (!state.dirty || !state.day || el("journal").hidden) return;
  state.dirty = false;
  try {
    const res = await sendJSON("PUT", "api/entry", collectEntry());
    state.entry = res.entry ? { ...res.entry, day: res.day, updated_at: res.saved_at } : null;
    setSaveState(res.deleted ? "Nothing written" : `Saved ${timeOf(res.saved_at)}`);
    // The add-on is the authority on tags — it lower-cases them, strips the
    // hash and drops duplicates — so the chips come back from what it stored,
    // not from what was typed.
    state.tags = state.entry ? state.entry.tags : [];
    renderTags();
    await refreshStats();
    await refreshCalendar();
  } catch (err) {
    if (String(err.message) !== "locked") {
      state.dirty = true;
      setSaveState("Not saved — check the add-on log");
    }
  }
}

function setSaveState(text) {
  el("save-state").textContent = text;
}

async function refreshStats() {
  const info = await fetchJSON("api/state");
  state.stats = info.stats;
  const pill = el("streak-pill");
  pill.hidden = false;
  pill.textContent = info.stats.streak ? `${info.stats.streak}-day streak` : `${info.stats.entries} written`;
  pill.className = `pill${info.stats.has_entry_today ? " pill-good" : ""}`;
}

// --- The twelve-week strip ---

async function refreshCalendar() {
  if (el("journal").hidden) return;
  const end = state.today;
  // Start on the Monday of the first week shown, so the columns are weeks and
  // the rows are weekdays rather than a ribbon that drifts.
  const endDate = parseDay(end);
  const start = new Date(endDate);
  start.setDate(start.getDate() - (CALENDAR_WEEKS * 7 - 1));
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));

  const data = await fetchJSON(`api/calendar?start=${isoDay(start)}&end=${end}`);
  const byDay = new Map(data.days.map((d) => [d.day, d]));
  const cells = [];
  for (let cursor = new Date(start); cursor <= endDate; cursor.setDate(cursor.getDate() + 1)) {
    const iso = isoDay(cursor);
    const entry = byDay.get(iso);
    const classes = ["cal-day"];
    if (entry) classes.push(entry.mood ? `cal-mood-${entry.mood}` : "cal-written");
    if (iso === state.today) classes.push("cal-today");
    if (iso === state.day) classes.push("cal-selected");
    const title = entry
      ? `${longDate(iso)} — ${entry.words} word${entry.words === 1 ? "" : "s"}${entry.mood ? `, ${MOODS[entry.mood - 1].label.toLowerCase()}` : ""}`
      : `${longDate(iso)} — nothing written`;
    cells.push(`<button type="button" class="${classes.join(" ")}" data-goto="${iso}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"></button>`);
  }
  el("calendar-host").innerHTML = cells.join("");
  el("calendar-count").textContent = `${data.days.length} of ${cells.length} days`;
}

// --- Search ---

let searchTimer = null;

function wireSearch() {
  el("search-open-btn").addEventListener("click", () => {
    openSheet("search-backdrop");
    el("search-input").focus();
  });
  el("search-input").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 300);
  });
}

async function runSearch() {
  const query = el("search-input").value.trim();
  const host = el("search-results");
  if (query.length < 2) {
    host.innerHTML = '<p class="empty-state">Type at least two characters.</p>';
    return;
  }
  const data = await fetchJSON(`api/search?q=${encodeURIComponent(query)}`);
  if (!data.results.length) {
    host.innerHTML = `<p class="empty-state">Nothing matches “${escapeHtml(query)}”.</p>`;
    return;
  }
  host.innerHTML = data.results.map((hit) => `
    <div class="recall">
      <div class="recall-head">
        <span class="recall-when">${escapeHtml(longDate(hit.day))}</span>
        <button type="button" class="recall-open" data-goto="${hit.day}" data-close-sheet="search-backdrop">open</button>
      </div>
      <div class="recall-section">${escapeHtml(hit.section)}</div>
      <p class="recall-body">${escapeHtml(hit.snippet)}</p>
    </div>`).join("");
}

// --- Goals ---

async function refreshGoalsSheet() {
  const data = await fetchJSON("api/goals");
  state.goals = data.goals;
  el("goals-manage-host").innerHTML = data.goals.map((goal) => `
    <div class="goal-row ${goal.status !== "active" ? "goal-done" : ""}">
      <div class="goal-head">
        <span class="goal-title">${escapeHtml(goal.title)}</span>
        <span class="pill">${escapeHtml(goal.status)}</span>
      </div>
      ${goal.why ? `<div class="goal-sub">${escapeHtml(goal.why)}</div>` : ""}
      <div class="goal-sub">${escapeHtml(goalSubtitle(goal))}</div>
      <div class="goal-actions">
        <button type="button" class="btn-secondary btn-small" data-timeline="${escapeHtml(goal.id)}">History</button>
        ${goal.status === "active"
          ? `<button type="button" class="btn-secondary btn-small" data-status="done" data-goal="${escapeHtml(goal.id)}">Done</button>
             <button type="button" class="btn-secondary btn-small" data-status="dropped" data-goal="${escapeHtml(goal.id)}">Drop</button>`
          : `<button type="button" class="btn-secondary btn-small" data-status="active" data-goal="${escapeHtml(goal.id)}">Reopen</button>`}
        <button type="button" class="btn-danger btn-small" data-delete-goal="${escapeHtml(goal.id)}">Delete</button>
      </div>
    </div>`).join("") || '<p class="empty-state">No goals yet.</p>';
  renderGoals();
}

async function showTimeline(goalId) {
  const goal = state.goals.find((g) => g.id === goalId);
  el("timeline-title").textContent = goal ? goal.title : "Goal";
  const data = await fetchJSON(`api/goals/${encodeURIComponent(goalId)}/timeline`);
  el("timeline-host").innerHTML = data.timeline.length
    ? data.timeline.map((point) => `
      <div class="recall">
        <div class="recall-head">
          <span class="recall-when">${escapeHtml(longDate(point.day))}${point.moved ? " ✓" : ""}</span>
          <button type="button" class="recall-open" data-goto="${point.day}" data-close-sheet="timeline-backdrop">open</button>
        </div>
        ${point.note ? `<p class="recall-body">${escapeHtml(point.note)}</p>` : '<p class="recall-body muted">Marked as moved.</p>'}
      </div>`).join("")
    : '<p class="empty-state">No check-ins against this goal yet.</p>';
  openSheet("timeline-backdrop");
}

// --- Settings ---

function renderSectionsEditor() {
  el("sections-editor").innerHTML = state.sections.map((section, index) => `
    <div class="section-edit" data-index="${index}">
      <div class="section-edit-fields">
        <input type="text" data-field="title" value="${escapeHtml(section.title)}" placeholder="Heading">
        <input type="text" data-field="hint" value="${escapeHtml(section.hint || "")}" placeholder="Hint (optional)">
      </div>
      <div class="section-edit-buttons">
        <button type="button" class="btn-secondary" data-move="-1" aria-label="Move up">↑</button>
        <button type="button" class="btn-secondary" data-move="1" aria-label="Move down">↓</button>
        <button type="button" class="btn-danger" data-remove="${index}" aria-label="Remove">✕</button>
      </div>
    </div>`).join("");
}

function readSectionsEditor() {
  return [...el("sections-editor").querySelectorAll(".section-edit")].map((row) => {
    const index = Number(row.dataset.index);
    const existing = state.sections[index] || {};
    return {
      // Keeping the key is what keeps old entries attached to a renamed
      // section. A new row has no key and gets one from its title.
      key: existing.key,
      title: row.querySelector('[data-field="title"]').value,
      hint: row.querySelector('[data-field="hint"]').value,
    };
  });
}

async function refreshSettingsStats() {
  const info = await fetchJSON("api/state");
  const s = info.stats;
  el("settings-stats").textContent = [
    `Entries: ${s.entries}`,
    `First: ${s.first_entry_on || "–"}`,
    `Last: ${s.last_entry_on || "–"}`,
    `Streak: ${s.streak} (longest ${s.longest_streak})`,
    `Goals: ${s.goals_active} active, ${s.goals_done} done`,
    `Auto-lock: ${info.auto_lock_minutes ? info.auto_lock_minutes + " min idle" : "never (padlock and restarts still lock)"}`,
    `Goal nudge: ${info.goal_nudge_days ? "after " + info.goal_nudge_days + " quiet days" : "off"}`,
    `Version: ${info.app_version}`,
  ].join("\n");
}

async function downloadBackup() {
  const res = await fetch("api/backup", { headers: { "X-Journal-Session": token() } });
  if (!res.ok) {
    showToastError("Backup failed.");
    return;
  }
  await saveBlob(await res.blob(), `journal-backup-${state.today}.db`);
}

async function restoreFromFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  // Clear the input either way, so picking the same file twice still fires a
  // change event — otherwise a cancelled restore cannot be retried.
  input.value = "";
  if (!file) return;

  const error = el("restore-error");
  error.hidden = true;
  if (!confirm(
    `Replace this journal with "${file.name}"?\n\n` +
    "Everything currently in it is deleted, including anything written since " +
    "that backup was taken. This cannot be undone.\n\n" +
    "The restored journal opens with the password it had when the backup was " +
    "made, which may not be the one you just used."
  )) return;

  const body = new FormData();
  body.append("file", file);
  const res = await fetch("api/restore", {
    method: "POST",
    headers: { "X-Journal-Session": token() },
    body,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    error.textContent = detail.detail || detail.error || "Restore failed.";
    error.hidden = false;
    return;
  }
  // The restore closed every session, this one included. Reloading is the
  // honest thing to do: the page is holding a token for a vault that is gone,
  // and the lock screen is where somebody has to start again anyway.
  window.location.reload();
}

async function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function downloadExport() {
  const res = await fetch("api/export", { headers: { "X-Journal-Session": token() } });
  if (!res.ok) {
    showToastError("Export failed.");
    return;
  }
  await saveBlob(await res.blob(), `journal-${state.today}.json`);
}

function showToastError(message) {
  // No toast framework here; settings errors have a home already.
  const box = el("pw-error");
  box.textContent = message;
  box.hidden = false;
}

// --- Sheets ---

function openSheet(id) {
  el(id).classList.add("open");
}

function closeSheet(id) {
  el(id).classList.remove("open");
}

function closeAllSheets() {
  document.querySelectorAll(".sheet-backdrop").forEach((b) => b.classList.remove("open"));
}

function wireSheets() {
  document.querySelectorAll("[data-close]").forEach((btn) => {
    btn.addEventListener("click", () => closeSheet(btn.dataset.close));
  });
  document.querySelectorAll(".sheet-backdrop").forEach((backdrop) => {
    backdrop.addEventListener("click", (e) => {
      if (e.target === backdrop) backdrop.classList.remove("open");
    });
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllSheets();
  });
}

// --- Wiring ---

function wireLockScreen() {
  el("setup-btn").addEventListener("click", async () => {
    const password = el("setup-password").value;
    if (password !== el("setup-password-2").value) {
      showError("The two passwords do not match.");
      return;
    }
    if (password.length < 8) {
      showError("Use at least 8 characters.");
      return;
    }
    try {
      const res = await sendJSON("POST", "api/vault", { password });
      setToken(res.token);
      el("setup-password").value = el("setup-password-2").value = "";
      await openJournal(state.today);
    } catch (err) {
      showError(err.message);
    }
  });

  el("unlock-btn").addEventListener("click", async () => {
    try {
      await unlock(el("unlock-password").value);
      el("unlock-password").value = "";
    } catch (err) {
      showError(err.message === "locked" ? "Wrong master password." : err.message);
    }
  });

  el("unlock-password").addEventListener("keydown", (e) => {
    if (e.key === "Enter") el("unlock-btn").click();
  });
  el("setup-password-2").addEventListener("keydown", (e) => {
    if (e.key === "Enter") el("setup-btn").click();
  });
}

function wireDayNavigation() {
  el("day-prev").addEventListener("click", () => loadDay(shiftDay(state.day, -1)));
  el("day-next").addEventListener("click", () => loadDay(shiftDay(state.day, 1)));
  el("day-today").addEventListener("click", () => loadDay(state.today));
  el("day-input").addEventListener("change", () => {
    if (el("day-input").value) loadDay(el("day-input").value);
  });
  // Everything that jumps to a date says so with data-goto, wherever it is.
  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-goto]");
    if (!target) return;
    if (target.dataset.closeSheet) closeSheet(target.dataset.closeSheet);
    loadDay(target.dataset.goto);
  });
}

function wireEntry() {
  el("save-btn").addEventListener("click", () => {
    state.dirty = true;
    flushSave();
  });
  el("tags-input").addEventListener("input", markDirty);
  el("tags-input").addEventListener("blur", () => {
    state.tags = parseTags(el("tags-input").value);
    renderTags();
    flushSave();
  });
  el("delete-btn").addEventListener("click", async () => {
    if (!confirm(`Delete everything written on ${longDate(state.day)}? This cannot be undone.`)) return;
    await sendJSON("PUT", "api/entry", { day: state.day, sections: [], mood: null, tags: [], goals: [] });
    await loadDay(state.day);
    await refreshCalendar();
  });
  // A tab closed or a phone locked mid-sentence should still have saved.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushSave();
  });
  window.addEventListener("pagehide", flushSave);
}

function wireGoals() {
  el("goals-open-btn").addEventListener("click", async () => {
    openSheet("goals-backdrop");
    await refreshGoalsSheet();
  });
  el("goal-add-btn").addEventListener("click", async () => {
    const title = el("goal-title").value.trim();
    if (!title) return;
    await sendJSON("POST", "api/goals", {
      title,
      why: el("goal-why").value,
      target_date: el("goal-target").value || null,
    });
    el("goal-title").value = el("goal-why").value = el("goal-target").value = "";
    await refreshGoalsSheet();
  });
  el("goals-manage-host").addEventListener("click", async (e) => {
    const timeline = e.target.closest("[data-timeline]");
    if (timeline) return showTimeline(timeline.dataset.timeline);
    const status = e.target.closest("[data-status]");
    if (status) {
      await sendJSON("PATCH", `api/goals/${encodeURIComponent(status.dataset.goal)}`, { status: status.dataset.status });
      await refreshGoalsSheet();
      return;
    }
    const remove = e.target.closest("[data-delete-goal]");
    if (remove) {
      if (!confirm("Delete this goal? What you wrote about it on each day stays in those days.")) return;
      await fetchJSON(`api/goals/${encodeURIComponent(remove.dataset.deleteGoal)}`, { method: "DELETE" });
      await refreshGoalsSheet();
    }
  });
}

function wireSettings() {
  el("settings-open-btn").addEventListener("click", async () => {
    openSheet("settings-backdrop");
    renderSectionsEditor();
    await refreshSettingsStats();
  });

  el("section-add-btn").addEventListener("click", () => {
    state.sections = readSectionsEditor().concat({ key: "", title: "", hint: "" });
    renderSectionsEditor();
  });

  el("sections-editor").addEventListener("click", (e) => {
    const move = e.target.closest("[data-move]");
    const remove = e.target.closest("[data-remove]");
    if (!move && !remove) return;
    const rows = readSectionsEditor();
    const index = Number((move || remove).closest(".section-edit").dataset.index);
    if (remove) {
      rows.splice(index, 1);
    } else {
      const to = index + Number(move.dataset.move);
      if (to < 0 || to >= rows.length) return;
      [rows[index], rows[to]] = [rows[to], rows[index]];
    }
    state.sections = rows;
    renderSectionsEditor();
  });

  el("sections-save-btn").addEventListener("click", async () => {
    try {
      const res = await sendJSON("PUT", "api/sections", { sections: readSectionsEditor() });
      state.sections = res.sections;
      renderSectionsEditor();
      await loadDay(state.day);
    } catch (err) {
      showToastError(err.message);
    }
  });

  el("pw-change-btn").addEventListener("click", async () => {
    const error = el("pw-error");
    error.hidden = true;
    if (el("pw-new").value !== el("pw-new-2").value) {
      showToastError("The two new passwords do not match.");
      return;
    }
    try {
      await sendJSON("POST", "api/password", {
        old_password: el("pw-old").value,
        new_password: el("pw-new").value,
      });
      el("pw-old").value = el("pw-new").value = el("pw-new-2").value = "";
      error.textContent = "Password changed. Every entry was re-encrypted.";
      error.hidden = false;
    } catch (err) {
      showToastError(err.message);
    }
  });

  el("export-btn").addEventListener("click", downloadExport);
  el("backup-btn").addEventListener("click", downloadBackup);
  // The button opens the file picker; the picker's change event does the work,
  // so nothing happens until a file has actually been chosen.
  el("restore-btn").addEventListener("click", () => el("restore-file").click());
  el("restore-file").addEventListener("change", restoreFromFile);
  el("lock-btn").addEventListener("click", lockNow);
}

async function main() {
  wireSheets();
  wireLockScreen();
  wireDayNavigation();
  wireEntry();
  wireGoals();
  wireSearch();
  wireSettings();
  try {
    await boot();
  } catch (err) {
    if (String(err.message) !== "locked") showError(err.message);
  }
}

main();
