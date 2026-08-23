"use strict";

const SELF_GRADE_LABELS = { got_it: "Got it", partly: "Partly", missed: "Missed it" };

const state = {
  summary: null,
  cards: [],
  cardIndex: 0,
  cardRevealed: false,
  promptTopicId: null,
  importTopicId: null,
};

function escapeHtml(str) {
  return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function fetchJSON(path, options) {
  const res = await fetch(path, options);
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    // A non-JSON body (a proxy error page, say) still has to surface as an
    // error the caller can show, not as a parse exception.
  }
  if (!res.ok) throw new Error((body && body.error) || `${path}: HTTP ${res.status}`);
  return body;
}

function postJSON(path, payload) {
  return fetchJSON(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function el(id) {
  return document.getElementById(id);
}

// --- Sheets ---

function openSheet(id) {
  el(id).classList.add("open");
}

function closeSheet(id) {
  el(id).classList.remove("open");
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
}

// --- Today's lesson ---

function renderLessons(summary) {
  const host = el("lessons");
  const empty = el("today-empty");
  el("today-date").textContent = summary.today;

  if (!summary.lessons.length) {
    host.innerHTML = "";
    empty.hidden = false;
    empty.innerHTML = summary.topics.length
      ? "Nothing scheduled — every subtopic with material has already had its day. " +
        "Load another pack from <strong>Topics</strong> below and today's lesson appears immediately."
      : "Subscribe to a topic below to get started.";
    return;
  }
  empty.hidden = true;
  host.innerHTML = summary.lessons.map(renderLesson).join("");
  wireLesson();
}

function renderLesson(lesson) {
  const s = lesson.subtopic;
  const mcq = lesson.questions.filter((q) => q.kind === "mcq");
  const short = lesson.questions.filter((q) => q.kind === "short");
  const done = lesson.completed_at;

  return `
    <article class="lesson" data-lesson="${lesson.id}">
      <div class="lesson-topic">${escapeHtml(lesson.topic.name)} · subtopic ${s.position}</div>
      <h3 class="lesson-title">${escapeHtml(s.title)}
        ${done ? '<span class="pill pill-done">done</span>' : ""}</h3>
      ${s.summary ? `<p class="lesson-summary">${escapeHtml(s.summary)}</p>` : ""}
      ${s.briefing ? `
        <details class="briefing-wrap" ${done ? "" : "open"}>
          <summary>Briefing</summary>
          <div class="briefing">${escapeHtml(s.briefing)}</div>
        </details>` : ""}
      ${mcq.length ? `<div class="section-label">Quiz — ${lesson.correct_count}/${lesson.graded_count || 0} right so far</div>
        ${mcq.map((q) => renderMcq(lesson.id, q)).join("")}` : ""}
      ${short.length ? `<div class="section-label">In your own words</div>
        ${short.map((q) => renderShort(lesson.id, q)).join("")}` : ""}
      ${s.practical_task ? `<div class="section-label">Do this</div>
        <div class="task">${escapeHtml(s.practical_task)}</div>` : ""}
      ${done ? "" : `<button type="button" class="btn-secondary full js-complete" data-lesson="${lesson.id}">
        Mark this subtopic done</button>`}
    </article>`;
}

function renderMcq(lessonId, q) {
  const answered = q.answered;
  const choices = (q.choices || []).map((choice, i) => {
    let cls = "choice";
    if (answered) {
      if (i === q.answer_index) cls += " choice-correct";
      else if (i === q.chosen_index) cls += " choice-wrong";
    }
    return `<button type="button" class="${cls}" data-lesson="${lessonId}" data-question="${q.id}"
      data-choice="${i}" ${answered ? "disabled" : ""}>${escapeHtml(choice)}</button>`;
  }).join("");
  return `
    <div class="q" data-question="${q.id}">
      <div class="q-text">${escapeHtml(q.question)}</div>
      <div class="choices">${choices}</div>
      ${answered && q.explanation ? `<div class="explanation">${escapeHtml(q.explanation)}</div>` : ""}
    </div>`;
}

function renderShort(lessonId, q) {
  if (q.answered) {
    return `
      <div class="q" data-question="${q.id}">
        <div class="q-text">${escapeHtml(q.question)}</div>
        ${q.response_text ? `<div class="explanation">You wrote: ${escapeHtml(q.response_text)}</div>` : ""}
        ${q.model_answer ? `<div class="explanation"><strong>Model answer:</strong> ${escapeHtml(q.model_answer)}</div>` : ""}
        <div class="muted">Graded: ${escapeHtml(SELF_GRADE_LABELS[q.self_grade] || "–")}</div>
      </div>`;
  }
  return `
    <div class="q" data-question="${q.id}">
      <div class="q-text">${escapeHtml(q.question)}</div>
      <textarea rows="3" class="js-short-text" data-question="${q.id}" placeholder="Answer in 2-4 sentences, then grade yourself against the model answer."></textarea>
      <div class="self-grade">
        <button type="button" class="btn-secondary js-reveal" data-question="${q.id}">Show model answer</button>
      </div>
      <div class="js-model" data-question="${q.id}" hidden></div>
    </div>`;
}

function wireLesson() {
  document.querySelectorAll(".choice:not([disabled])").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.closest(".choices").querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        await postJSON("api/answers", {
          lesson_id: Number(btn.dataset.lesson),
          question_id: Number(btn.dataset.question),
          chosen_index: Number(btn.dataset.choice),
        });
        await loadSummary();
      } catch (err) {
        alertInline(btn.closest(".q"), String(err));
      }
    });
  });

  // A short-answer question is only gradeable once you have seen what a good
  // answer contains — revealing it is what turns the three buttons on, so the
  // grade is a comparison rather than a guess about your own memory.
  document.querySelectorAll(".js-reveal").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const qid = btn.dataset.question;
      const lesson = btn.closest(".lesson");
      const lessonId = Number(lesson.dataset.lesson);
      const host = lesson.querySelector(`.js-model[data-question="${qid}"]`);
      host.hidden = false;
      let revealed = {};
      try {
        revealed = await fetchJSON(`api/questions/${qid}/reveal`);
      } catch (err) {
        alertInline(host, String(err));
        return;
      }
      host.innerHTML =
        (revealed.model_answer
          ? `<div class="explanation"><strong>Model answer:</strong> ${escapeHtml(revealed.model_answer)}</div>`
          : `<div class="explanation">This question came without a model answer — grade yourself on whether you could explain it out loud.</div>`) +
        `<div class="self-grade">
           ${["got_it", "partly", "missed"].map((g) => `
             <button type="button" class="btn-secondary js-grade" data-lesson="${lessonId}"
               data-question="${qid}" data-grade="${g}">${SELF_GRADE_LABELS[g]}</button>`).join("")}
         </div>`;
      btn.disabled = true;
      host.querySelectorAll(".js-grade").forEach((gradeBtn) => {
        gradeBtn.addEventListener("click", async () => {
          const text = lesson.querySelector(`.js-short-text[data-question="${qid}"]`);
          try {
            await postJSON("api/answers", {
              lesson_id: Number(gradeBtn.dataset.lesson),
              question_id: Number(gradeBtn.dataset.question),
              self_grade: gradeBtn.dataset.grade,
              response_text: text ? text.value : null,
            });
            await loadSummary();
          } catch (err) {
            alertInline(host, String(err));
          }
        });
      });
    });
  });

  document.querySelectorAll(".js-complete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await postJSON(`api/lessons/${btn.dataset.lesson}/complete`, {});
        await loadSummary();
      } catch (err) {
        alertInline(btn.parentElement, String(err));
        btn.disabled = false;
      }
    });
  });
}

function alertInline(host, message) {
  if (!host) return;
  const div = document.createElement("div");
  div.className = "notice notice-bad";
  div.textContent = message;
  host.appendChild(div);
}

// --- Flashcard review ---

async function loadCards() {
  try {
    state.cards = await fetchJSON("api/cards/due");
  } catch (_) {
    state.cards = [];
  }
  state.cardIndex = 0;
  state.cardRevealed = false;
  renderReview();
}

function renderReview() {
  const card = el("review-card");
  const body = el("review-body");
  const remaining = state.cards.length - state.cardIndex;

  if (remaining <= 0) {
    if (state.cards.length) {
      card.hidden = false;
      el("review-remaining").textContent = "all done";
      body.innerHTML = '<p class="empty-state">Every card due today is reviewed. The next ones come back on their own schedule.</p>';
    } else {
      card.hidden = true;
    }
    return;
  }

  card.hidden = false;
  el("review-remaining").textContent = `${remaining} left`;
  const current = state.cards[state.cardIndex];
  body.innerHTML = `
    <div class="flashcard">
      <div class="flashcard-front">${escapeHtml(current.front)}</div>
      ${state.cardRevealed ? `<div class="flashcard-back">${escapeHtml(current.back)}</div>` : ""}
      <div class="flashcard-source">${escapeHtml(current.topic)} · ${escapeHtml(current.subtopic)}</div>
    </div>
    ${state.cardRevealed ? `
      <div class="grade-row">
        <button type="button" class="btn-secondary btn-again" data-grade="again">Again</button>
        <button type="button" class="btn-secondary btn-hard" data-grade="hard">Hard</button>
        <button type="button" class="btn-secondary btn-good" data-grade="good">Good</button>
        <button type="button" class="btn-secondary btn-easy" data-grade="easy">Easy</button>
      </div>`
    : '<button type="button" class="btn-primary full" id="reveal-card-btn">Show answer</button>'}`;

  const reveal = el("reveal-card-btn");
  if (reveal) {
    reveal.addEventListener("click", () => {
      state.cardRevealed = true;
      renderReview();
    });
  }
  body.querySelectorAll("[data-grade]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      body.querySelectorAll("[data-grade]").forEach((b) => (b.disabled = true));
      try {
        await postJSON(`api/cards/${current.id}/review`, { grade: btn.dataset.grade });
      } catch (err) {
        alertInline(body, String(err));
        return;
      }
      if (btn.dataset.grade === "again") {
        // A lapsed card is due again today, so it belongs at the back of this
        // same queue rather than disappearing until tomorrow.
        state.cards.push(current);
      }
      state.cardIndex += 1;
      state.cardRevealed = false;
      renderReview();
      loadSummary();
    });
  });
}

// --- Topics ---

function renderTopics(summary) {
  const host = el("topics-list");
  const empty = el("topics-empty");
  if (!summary.topics.length) {
    host.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  host.innerHTML = summary.topics.map((t) => {
    const pct = t.subtopics_total ? Math.round((t.subtopics_served / t.subtopics_total) * 100) : 0;
    const low = t.days_of_material_left <= summary.config.low_material_threshold;
    const needsSyllabus = t.subtopics_total === 0;
    return `
      <div class="topic-row">
        <div class="topic-head">
          <span class="topic-name">${escapeHtml(t.name)}</span>
          <span class="pill ${low ? "pill-warn" : "pill-accent"}">${t.days_of_material_left} d left</span>
        </div>
        <div class="topic-meta">${t.subtopics_served} of ${t.subtopics_total || "?"} subtopics ·
          ${escapeHtml(t.level)}${t.active ? "" : " · paused"}</div>
        <div class="bar"><div class="bar-fill" style="width:${pct}%"></div></div>
        <div class="topic-actions">
          <button type="button" class="btn-secondary btn-small js-prompt" data-topic="${t.id}">
            ${needsSyllabus ? "Get the first prompt" : "Get a prompt"}</button>
          <button type="button" class="btn-secondary btn-small js-import" data-topic="${t.id}">Load a pack</button>
          <button type="button" class="btn-secondary btn-small js-toggle" data-topic="${t.id}"
            data-active="${t.active ? 1 : 0}">${t.active ? "Pause" : "Resume"}</button>
          <button type="button" class="btn-secondary btn-small js-delete" data-topic="${t.id}"
            data-name="${escapeHtml(t.name)}">Remove</button>
        </div>
      </div>`;
  }).join("");

  host.querySelectorAll(".js-prompt").forEach((btn) => {
    btn.addEventListener("click", () => showPrompt(Number(btn.dataset.topic)));
  });
  host.querySelectorAll(".js-import").forEach((btn) => {
    btn.addEventListener("click", () => showImport(Number(btn.dataset.topic)));
  });
  host.querySelectorAll(".js-toggle").forEach((btn) => {
    btn.addEventListener("click", async () => {
      await fetchJSON(`api/topics/${btn.dataset.topic}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ active: btn.dataset.active !== "1" }),
      });
      loadSummary();
    });
  });
  host.querySelectorAll(".js-delete").forEach((btn) => {
    btn.addEventListener("click", async () => {
      // Deleting a topic throws away its whole history — every card's review
      // schedule, every answer. That is worth one deliberate confirmation.
      if (!window.confirm(`Remove "${btn.dataset.name}" and everything studied under it?`)) return;
      await fetchJSON(`api/topics/${btn.dataset.topic}`, { method: "DELETE" });
      loadSummary();
    });
  });
}

// --- Prompt sheet ---

async function showPrompt(topicId, kind) {
  state.promptTopicId = topicId;
  openSheet("prompt-backdrop");
  el("prompt-text").textContent = "Building the prompt…";
  el("prompt-intro").textContent = "";
  try {
    const data = await fetchJSON(`api/topics/${topicId}/prompt${kind ? `?kind=${kind}` : ""}`);
    el("prompt-title").textContent = `Prompt — ${data.topic}`;
    el("prompt-text").textContent = data.prompt;
    el("prompt-intro").innerHTML = {
      new: "Copy this into any assistant — on your phone, at work, anywhere with a connection. It asks for the whole syllabus plus the first stretch of material. Paste the reply back here afterwards.",
      more: `Copy this into any assistant. It asks for material for the ${(data.covers || []).length} subtopics you have not received yet, and includes your existing syllabus so the depth matches.`,
      extend: "You have finished this syllabus. This prompt asks for a harder set of subtopics that go beyond it, with their material.",
    }[data.kind] || "";
    state.promptFilename = data.filename;
    state.promptBody = data.prompt;
  } catch (err) {
    el("prompt-text").textContent = String(err);
  }
}

function wirePromptSheet() {
  el("prompt-copy-btn").addEventListener("click", async () => {
    const btn = el("prompt-copy-btn");
    try {
      await navigator.clipboard.writeText(state.promptBody || "");
      btn.textContent = "Copied ✓";
    } catch (_) {
      // Ingress is served over the parent page's origin and clipboard access
      // can be refused there; selecting the text is the fallback that always
      // works, so say so rather than failing silently.
      btn.textContent = "Select the text below and copy";
    }
    setTimeout(() => (btn.textContent = "Copy prompt"), 2500);
  });

  el("prompt-download-btn").addEventListener("click", () => {
    const blob = new Blob([state.promptBody || ""], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = state.promptFilename || "knowledge-prompt.txt";
    a.click();
    URL.revokeObjectURL(url);
  });

  el("prompt-goto-import-btn").addEventListener("click", () => {
    closeSheet("prompt-backdrop");
    showImport(state.promptTopicId);
  });
}

// --- Import sheet ---

function showImport(topicId) {
  state.importTopicId = topicId;
  const topic = (state.summary && state.summary.topics.find((t) => t.id === topicId)) || null;
  el("import-intro").textContent = topic
    ? `Paste the assistant's reply for "${topic.name}". Anything unusable is skipped and listed rather than failing the whole pack.`
    : "Paste the assistant's reply.";
  el("import-result").innerHTML = "";
  el("import-text").value = "";
  openSheet("import-backdrop");
}

function renderImportReport(report) {
  const host = el("import-result");
  const warnings = report.warnings || [];
  const parts = [];
  if (report.subtopics_added != null) {
    parts.push(`
      <div class="notice notice-ok">
        Loaded: ${report.subtopics_added} new subtopics, ${report.material_added} with material,
        ${report.questions_added} questions, ${report.cards_added} flashcards.
      </div>`);
  } else {
    parts.push(`
      <div class="notice notice-ok">
        Looks good: ${report.syllabus_count} syllabus entries, ${report.material_count} with material,
        ${report.question_count} questions, ${report.card_count} flashcards. Nothing saved yet.
      </div>`);
  }
  if (warnings.length) {
    parts.push(`<div class="notice notice-warn"><strong>Skipped ${warnings.length}:</strong><br>` +
      warnings.map((w) => escapeHtml(w)).join("<br>") + "</div>");
  }
  host.innerHTML = parts.join("");
}

function wireImportSheet() {
  el("import-check-btn").addEventListener("click", async () => {
    try {
      renderImportReport(await postJSON("api/import/preview", { text: el("import-text").value }));
    } catch (err) {
      el("import-result").innerHTML = `<div class="notice notice-bad">${escapeHtml(String(err))}</div>`;
    }
  });

  el("import-save-btn").addEventListener("click", async () => {
    const btn = el("import-save-btn");
    btn.disabled = true;
    try {
      const report = await postJSON("api/import", {
        topic_id: state.importTopicId,
        text: el("import-text").value,
      });
      renderImportReport(report);
      el("import-text").value = "";
      await loadSummary();
      await loadCards();
    } catch (err) {
      el("import-result").innerHTML = `<div class="notice notice-bad">${escapeHtml(String(err))}</div>`;
    } finally {
      btn.disabled = false;
    }
  });

  el("import-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    el("import-text").value = await file.text();
    el("import-result").innerHTML =
      '<div class="notice notice-ok">File read into the box above — check it, then load it.</div>';
    e.target.value = "";
  });
}

// --- Add topic ---

function wireAddTopic() {
  el("add-topic-btn").addEventListener("click", () => {
    el("add-name").value = "";
    el("add-goal").value = "";
    el("add-error").textContent = "";
    if (state.summary) el("add-level").value = state.summary.config.default_level;
    openSheet("add-backdrop");
  });

  el("add-save-btn").addEventListener("click", async () => {
    const btn = el("add-save-btn");
    btn.disabled = true;
    try {
      const topic = await postJSON("api/topics", {
        name: el("add-name").value,
        goal: el("add-goal").value,
        level: el("add-level").value,
      });
      closeSheet("add-backdrop");
      await loadSummary();
      // Straight into the prompt: a topic with no material does nothing, and
      // the next step is always the same one.
      showPrompt(topic.id, "new");
    } catch (err) {
      el("add-error").textContent = String(err);
    } finally {
      btn.disabled = false;
    }
  });
}

// --- Settings ---

function wireSettings() {
  el("settings-open-btn").addEventListener("click", async () => {
    openSheet("settings-backdrop");
    if (state.summary) {
      const cfg = state.summary.config;
      el("settings-config").textContent =
        `Subtopics per day: ${cfg.lessons_per_day}\n` +
        `Syllabus asked for: ${cfg.syllabus_size} subtopics\n` +
        `Material per pack: ${cfg.material_days} days\n` +
        `Per subtopic: ${cfg.quiz_questions} quiz, ${cfg.short_questions} short answer, ${cfg.flashcards} flashcards\n` +
        `Cards per review: ${cfg.cards_per_day}\n` +
        `Warn below: ${cfg.low_material_threshold} days of material\n` +
        `Daily reminder: ${cfg.reminder_enabled ? `on at ${cfg.reminder_time}` : "off"}`;
    }
    try {
      const stats = await fetchJSON("api/stats");
      el("settings-stats").textContent = Object.entries(stats.counts)
        .map(([table, n]) => `${table}: ${n}`)
        .join("\n") + `\n\nDatabase: ${Math.round((stats.db_bytes || 0) / 1024)} KB`;
    } catch (err) {
      el("settings-stats").textContent = String(err);
    }
  });

  el("notify-test-btn").addEventListener("click", async () => {
    const out = el("notify-result");
    out.innerHTML = "";
    try {
      const data = await fetchJSON("api/notify-services");
      out.innerHTML = data.services && data.services.length
        ? `<p class="muted">Put one of these in <code>notify_service</code>:</p>
           <div class="diag-output">${escapeHtml(data.services.join("\n"))}</div>`
        : `<div class="diag-output">${escapeHtml(data.error || "No notify services found.")}</div>`;
    } catch (err) {
      out.innerHTML = `<div class="diag-output">${escapeHtml(String(err))}</div>`;
    }
  });
}

// --- Load ---

function renderStats(summary) {
  const s = summary.stats;
  el("p-streak").textContent = s.streak_days;
  el("p-lessons").textContent = s.lessons_completed;
  el("p-accuracy").textContent = s.accuracy == null ? "–" : `${Math.round(s.accuracy * 100)}%`;
  el("p-cards").textContent = s.cards_due;
  el("streak-pill").textContent = s.streak_days ? `🔥 ${s.streak_days}` : "no streak";

  const warning = summary.material_warning;
  el("foot-note").textContent = warning
    ? `${warning.topic}: ${warning.days_left} days of material left`
    : "";
}

async function loadSummary() {
  let data;
  try {
    data = await fetchJSON("api/summary");
  } catch (err) {
    el("today-empty").hidden = false;
    el("today-empty").textContent = `Could not load: ${err}`;
    return;
  }
  state.summary = data;
  renderLessons(data);
  renderStats(data);
  renderTopics(data);
}

function init() {
  wireSheets();
  wireAddTopic();
  wirePromptSheet();
  wireImportSheet();
  wireSettings();
  loadSummary().then(loadCards);
  // The day rolls over in the background loop; this is what makes a tab left
  // open overnight show the new day's subtopic without a manual reload.
  setInterval(loadSummary, 300000);
}

document.addEventListener("DOMContentLoaded", init);
