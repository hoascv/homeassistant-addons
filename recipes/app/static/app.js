// The page. Plain DOM, no framework, no build step — the same shape as the
// other add-ons here.
//
// Everything interesting happens on the server, so this file fetches, renders
// and wires clicks. It holds no rules of its own: the list it draws has already
// been scaled, merged, grouped and flagged before it arrives.

const state = { summary: null, recipes: [], category: null, query: "", plan: null };

function el(id) { return document.getElementById(id); }

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  let body = null;
  try { body = await response.json(); } catch (e) { /* no body */ }
  if (!response.ok) throw new Error((body && body.error) || `server returned ${response.status}`);
  return body;
}

let toastTimer = null;
function toast(message) {
  const box = el("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 3200);
}

// Danish writes 1,5 rather than 1.5. The server produces a value; turning it
// into the local convention is the page's job.
function amount(text) { return String(text || "").replace(".", ","); }


// --- tabs and sheets ---------------------------------------------------------

function showTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("tab-on", tab.dataset.tab === name);
  }
  el("tab-browse").hidden = name !== "browse";
  el("tab-list").hidden = name !== "list";
  if (name === "list") loadPlan();
}

function openSheet(id) { el(id).hidden = false; }
function closeSheet(id) { el(id).hidden = true; }

document.addEventListener("click", (event) => {
  const closer = event.target.closest("[data-close]");
  // Only the backdrop itself closes, not a click that happened to land inside
  // the sheet sitting on top of it.
  if (closer && (event.target === closer || closer.tagName === "BUTTON")) {
    closeSheet(closer.dataset.close);
  }
  const tab = event.target.closest(".tab");
  if (tab) showTab(tab.dataset.tab);
});


// --- recipes -----------------------------------------------------------------

async function loadSummary() {
  state.summary = await fetchJSON("api/summary");
  const filter = el("category-filter");
  const all = ['<button type="button" class="seg-btn seg-on" data-category="">All</button>'];
  for (const category of state.summary.categories) {
    all.push(`<button type="button" class="seg-btn" data-category="${escapeHtml(category)}">`
             + `${escapeHtml(category)}</button>`);
  }
  filter.innerHTML = all.join("");

  const select = el("prompt-category");
  select.innerHTML = state.summary.categories
    .map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
}

async function loadRecipes() {
  const params = new URLSearchParams();
  if (state.category) params.set("category", state.category);
  if (state.query) params.set("q", state.query);
  state.recipes = await fetchJSON(`api/recipes?${params}`);
  renderRecipes();
}

function renderRecipes() {
  const host = el("recipe-list");
  el("recipe-empty").hidden = state.recipes.length > 0;
  host.innerHTML = state.recipes.map((r) => {
    const facts = [
      r.servings ? `${r.servings} servings` : null,
      r.minutes ? `${r.minutes} min` : null,
      // Only shown when the recipe actually carries it. A blank is honest;
      // a zero would read as a measurement.
      r.protein_g ? `${Math.round(r.protein_g)} g protein` : null,
    ].filter(Boolean).join(" · ");
    return `
      <div class="recipe-row" data-id="${r.id}">
        <div class="recipe-main">
          <span class="recipe-name">${escapeHtml(r.name)}</span>
          <span class="recipe-meta">${escapeHtml(facts)}</span>
        </div>
        <span class="pill">${escapeHtml(r.category)}</span>
      </div>`;
  }).join("");
}

el("recipe-list").addEventListener("click", (event) => {
  const row = event.target.closest(".recipe-row");
  if (row) openRecipe(Number(row.dataset.id));
});

el("category-filter").addEventListener("click", (event) => {
  const button = event.target.closest(".seg-btn");
  if (!button) return;
  for (const other of el("category-filter").children) other.classList.remove("seg-on");
  button.classList.add("seg-on");
  state.category = button.dataset.category || null;
  loadRecipes();
});

let searchTimer = null;
el("search").addEventListener("input", (event) => {
  state.query = event.target.value.trim();
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadRecipes, 200);
});


// --- one recipe --------------------------------------------------------------

async function openRecipe(id) {
  const recipe = await fetchJSON(`api/recipes/${id}`);
  const servings = recipe.servings || state.summary.default_servings;
  const facts = [
    recipe.servings ? `${recipe.servings} servings` : null,
    recipe.minutes ? `${recipe.minutes} min` : null,
    recipe.protein_g ? `${Math.round(recipe.protein_g)} g protein` : null,
    recipe.kcal ? `${Math.round(recipe.kcal)} kcal` : null,
  ].filter(Boolean).join(" · ");

  el("detail-title").textContent = recipe.name;
  el("detail-body").innerHTML = `
    <p class="muted">${escapeHtml(facts)}</p>
    ${recipe.notes ? `<p class="notes">${escapeHtml(recipe.notes)}</p>` : ""}
    <h3>Ingredients</h3>
    <ul class="ingredients">
      ${recipe.ingredients.map((i) => `
        <li>
          <span class="ing-amount">${escapeHtml(amount(i.amount ?? ""))} ${escapeHtml(i.unit)}</span>
          <span>${escapeHtml(i.name)}${i.optional ? " <em>(optional)</em>" : ""}</span>
          ${i.shop_name && i.shop_name !== i.name
            ? `<span class="ing-shop">${escapeHtml(i.shop_name)}</span>` : ""}
        </li>`).join("")}
    </ul>
    ${recipe.method ? `<h3>Method</h3><pre class="method">${escapeHtml(recipe.method)}</pre>` : ""}
    <div class="row">
      <label class="field field-inline">
        <span>Servings</span>
        <input type="number" id="add-servings" min="1" max="24" value="${servings}">
      </label>
      <button type="button" class="btn-primary" id="add-to-list" data-id="${recipe.id}">
        Add to list</button>
    </div>
    <button type="button" class="link-btn danger" id="delete-recipe" data-id="${recipe.id}">
      Delete this recipe</button>`;
  openSheet("recipe-backdrop");
}

el("detail-body").addEventListener("click", async (event) => {
  const add = event.target.closest("#add-to-list");
  if (add) {
    await fetchJSON("api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipe_id: Number(add.dataset.id),
                             servings: Number(el("add-servings").value) || 4 }),
    });
    closeSheet("recipe-backdrop");
    toast("Added to the list.");
    refreshCount();
    return;
  }
  const remove = event.target.closest("#delete-recipe");
  if (remove && confirm("Delete this recipe? This cannot be undone.")) {
    await fetchJSON(`api/recipes/${remove.dataset.id}`, { method: "DELETE" });
    closeSheet("recipe-backdrop");
    loadRecipes();
  }
});


// --- the shopping list -------------------------------------------------------

async function loadPlan() {
  state.plan = await fetchJSON("api/plan");
  renderPlan();
}

function renderPlan() {
  const { entries, list } = state.plan;
  el("planned").innerHTML = entries.map((e) => `
    <div class="planned-row">
      <span>${escapeHtml(e.recipe)} <em>· ${e.servings} servings</em></span>
      <button type="button" class="link-btn" data-remove="${e.recipe_id}">remove</button>
    </div>`).join("");

  // Always "n of m", never a bare count: the staples are on the list and
  // saying so is what stops the number reading as more shopping than it is.
  el("list-summary").textContent = entries.length
    ? `${list.total_items} items from ${list.recipes} recipes`
      + (list.staple_items ? `, ${list.staple_items} of them staples` : "")
      + ` · ${list.remaining_items} left`
    : "Nothing planned yet.";

  el("shopping").innerHTML = list.sections.map((section) => `
    <h3 class="section-head">${escapeHtml(section.section)}</h3>
    <ul class="shopping-items">
      ${section.items.map((item) => `
        <li class="shop-item${item.ticked ? " ticked" : ""}${item.staple ? " staple" : ""}"
            data-key="${item.key}">
          <input type="checkbox" ${item.ticked ? "checked" : ""} aria-label="${escapeHtml(item.name)}">
          <span class="shop-amount">${escapeHtml(amount(item.amount_text))} ${escapeHtml(item.unit)}</span>
          <span class="shop-name">${escapeHtml(item.name)}${item.optional ? " <em>(optional)</em>" : ""}
            <span class="shop-en">${escapeHtml(item.as_written.join(", "))}</span></span>
        </li>`).join("")}
    </ul>`).join("");

  refreshCount();
}

function refreshCount() {
  const badge = el("list-count");
  const remaining = state.plan ? state.plan.list.remaining_items : 0;
  badge.textContent = remaining;
  badge.hidden = !remaining;
}

el("shopping").addEventListener("change", async (event) => {
  const item = event.target.closest(".shop-item");
  if (!item) return;
  const ticked = event.target.checked;
  item.classList.toggle("ticked", ticked);
  try {
    await fetchJSON("api/list/tick", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: item.dataset.key, ticked }),
    });
    state.plan.list.remaining_items += ticked ? -1 : 1;
    refreshCount();
  } catch (err) { toast(err.message); }
});

el("planned").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  state.plan = await fetchJSON(`api/plan/${button.dataset.remove}`, { method: "DELETE" });
  renderPlan();
});

el("clear-plan").addEventListener("click", async () => {
  if (!confirm("Clear the whole list?")) return;
  state.plan = await fetchJSON("api/plan", { method: "DELETE" });
  renderPlan();
});


// --- loading recipes ---------------------------------------------------------

async function refreshPrompt() {
  const params = new URLSearchParams({
    kind: el("prompt-kind").value,
    category: el("prompt-category").value,
  });
  const body = await fetchJSON(`api/prompt?${params}`);
  el("prompt-text").textContent = body.prompt;
}

el("import-btn").addEventListener("click", async () => {
  openSheet("import-backdrop");
  el("import-report").innerHTML = "";
  await refreshPrompt();
});
el("prompt-kind").addEventListener("change", refreshPrompt);
el("prompt-category").addEventListener("change", refreshPrompt);

// Copying, in a place where the modern way does not work.
//
// navigator.clipboard requires a *secure context*, and Home Assistant ingress
// is served over plain http — so on most installs the modern API is simply
// absent, and the prompt this button exists to copy is several hundred words
// nobody wants to select by hand on a phone.
//
// document.execCommand("copy") is deprecated and is the only thing that works
// here. It needs a real, selectable element: display:none cannot be selected,
// so the textarea is placed off-screen instead. iOS additionally ignores
// .select() on a readonly textarea and needs an explicit range.
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) { /* fall through to the old way */ }
  }

  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);

  let copied = false;
  try {
    area.select();
    // iOS: .select() alone does nothing on a readonly field.
    area.setSelectionRange(0, text.length);
    copied = document.execCommand("copy");
  } catch (err) {
    copied = false;
  } finally {
    area.remove();
  }
  return copied;
}

el("copy-prompt").addEventListener("click", async () => {
  const details = el("prompt-text").closest("details");
  if (await copyText(el("prompt-text").textContent)) {
    toast("Prompt copied.");
    return;
  }
  // Both ways refused. Open the prompt and select it, so the only thing left
  // to do is press copy — rather than asking someone to drag-select six
  // paragraphs on a phone.
  details.open = true;
  const range = document.createRange();
  range.selectNodeContents(el("prompt-text"));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  toast("Could not copy automatically — the prompt is selected below.");
});

async function sendImport(path) {
  const report = el("import-report");
  try {
    const body = await fetchJSON(path, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: el("import-text").value,
                             category: el("prompt-category").value }),
    });
    const headline = body.added != null
      ? `Loaded ${body.added} of ${body.recipes} recipes.`
      : `Looks good: ${body.recipes.length} recipes. Nothing saved yet.`;
    report.innerHTML = `<div class="notice notice-ok">${escapeHtml(headline)}</div>`
      + (body.warnings && body.warnings.length
        ? `<div class="notice notice-warn"><strong>Notes:</strong><br>`
          + body.warnings.map(escapeHtml).join("<br>") + "</div>"
        : "");
    if (body.added) { loadRecipes(); loadSummary(); }
  } catch (err) {
    report.innerHTML = `<div class="notice notice-warn">${escapeHtml(err.message)}</div>`;
  }
}

el("check-import").addEventListener("click", () => sendImport("api/import/preview"));
el("do-import").addEventListener("click", () => sendImport("api/import"));


// --- boot --------------------------------------------------------------------

(async function start() {
  await loadSummary();
  await loadRecipes();
  await loadPlan();
})();
