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
  toastTimer = setTimeout(() => { el.hidden = true; }, Math.min(7000, Math.max(2600, msg.length * 60)));
}

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

// --- Physics constants -------------------------------------------------
// Coordinates are in world units, where 1 unit = 1 grid cell. A level's
// scroll_speed/length_units and every object's x/y/w/h use this scale —
// the renderer is the only place that knows how many pixels a unit is.

const PHYSICS_HZ = 240;
const FIXED_DT = 1000 / PHYSICS_HZ;   // ms per physics step
const MAX_FRAME_MS = 250;             // clamp a huge rAF gap (tab throttle, GC pause)
const MAX_STEPS_PER_FRAME = 64;       // defensive cap alongside MAX_FRAME_MS

const PLAYER_SIZE = 0.9;              // visual size, in units — a hair under one cell
const HITBOX_SCALE = 0.8;             // fraction of PLAYER_SIZE used for collision (fairness inset)
const GRAVITY = 22;                   // units/s^2
const JUMP_SPEED = 9.5;               // units/s, upward
const KILL_Y = -8;                    // fall this far below y=0 and it's a death, not a landing
const ROTATE_SPEED = Math.PI * 1.6;   // radians/s while airborne, cube-flip cosmetic
const COLLISION_WINDOW_AHEAD = 20;    // units ahead of the player still worth testing
const COLLISION_WINDOW_BEHIND = 5;    // units behind — objects further back than this are dropped

const VIEW_HEIGHT_UNITS = 8;          // world units visible top-to-bottom of the canvas
const GROUND_FRACTION = 0.75;         // ground line sits 75% down the canvas
const CAMERA_X_FRACTION = 0.3;        // player sits 30% in from the left edge

// --- Mode controllers --------------------------------------------------
// Every mode implements step(state, dtSec); the physics loop never branches
// on the mode name itself, so Ship/Ball (M4) are additive, not a rewrite.

function cubeStep(state, dtSec) {
  const p = state.player;
  if (p.grounded && state.input.justPressed) {
    p.vy = JUMP_SPEED;
    p.grounded = false;
    sfxJump();
  }

  p.vy -= GRAVITY * dtSec;
  p.y += p.vy * dtSec;

  if (!p.grounded) p.angle += ROTATE_SPEED * dtSec;

  const ground = groundSurfaceUnderPlayer(state);
  if (ground !== null && p.vy <= 0 && p.y <= ground) {
    p.y = ground;
    p.vy = 0;
    p.grounded = true;
    p.angle = Math.round(p.angle / (Math.PI / 2)) * (Math.PI / 2);
  } else {
    p.grounded = false;
  }
}

const MODES = { cube: { step: cubeStep } };
function currentController(state) {
  return MODES[state.player.mode] || MODES.cube;
}

// --- Level runtime & collision -----------------------------------------

function buildLevelRuntime(levelData) {
  const objects = (levelData.objects || []).slice().sort((a, b) => a.x - b.x);
  return {
    scrollSpeed: levelData.scroll_speed,
    lengthUnits: levelData.length_units,
    startMode: levelData.start_mode,
    background: levelData.background,
    objects,
    windowStart: 0,
  };
}

// Drops objects that have fully scrolled behind the player from the front of
// the window pointer, so per-step collision only tests what's nearby rather
// than the whole level — cheap now, matters once the editor allows bigger
// levels than the two seeded ones.
function advanceWindow(state) {
  const lvl = state.level;
  const minX = state.player.x - COLLISION_WINDOW_BEHIND;
  while (lvl.windowStart < lvl.objects.length) {
    const obj = lvl.objects[lvl.windowStart];
    if (obj.x + (obj.w || 0) < minX) lvl.windowStart += 1;
    else break;
  }
}

function windowObjects(state) {
  const lvl = state.level;
  const maxX = state.player.x + COLLISION_WINDOW_AHEAD;
  const out = [];
  for (let i = lvl.windowStart; i < lvl.objects.length; i++) {
    const obj = lvl.objects[i];
    if (obj.x > maxX) break;
    out.push(obj);
  }
  return out;
}

function playerHitboxX(state) {
  const half = (PLAYER_SIZE * HITBOX_SCALE) / 2;
  return [state.player.x - half, state.player.x + half];
}

// Highest block top surface overlapping the player's x-range — null means
// there's nothing underfoot (a gap), so gravity keeps pulling down.
function groundSurfaceUnderPlayer(state) {
  const [left, right] = playerHitboxX(state);
  let best = null;
  for (const obj of windowObjects(state)) {
    if (obj.type !== "block") continue;
    const objLeft = obj.x, objRight = obj.x + obj.w;
    if (objRight <= left || objLeft >= right) continue;
    const top = obj.y + obj.h;
    if (best === null || top > best) best = top;
  }
  return best;
}

function checkHazards(state) {
  const p = state.player;
  if (p.y < KILL_Y) { killPlayer(); return; }
  const [left, right] = playerHitboxX(state);
  const bottom = p.y, top = p.y + PLAYER_SIZE * HITBOX_SCALE;
  for (const obj of windowObjects(state)) {
    if (obj.type !== "spike") continue;
    const objLeft = obj.x, objRight = obj.x + obj.w;
    const objBottom = obj.y, objTop = obj.y + obj.h;
    if (objRight <= left || objLeft >= right) continue;
    if (objTop <= bottom || objBottom >= top) continue;
    killPlayer();
    return;
  }
}

function checkFinish(state) {
  if (state.player.x >= state.level.lengthUnits) finishLevel();
}

// --- Game state ----------------------------------------------------------

const state = {
  level: null,
  player: { mode: "cube", x: 0, y: 0, vy: 0, angle: 0, grounded: true, gravityDir: 1 },
  input: { pressed: false, justPressed: false, justReleased: false },
  run: { phase: "ready", attempts: 0, deaths: 0 }, // ready|running|dead|paused|finished
  particles: [],
};

let last = null;
let accumulator = 0;
let rafHandle = null;

function stepPhysics(dtSec) {
  const p = state.player;
  p.x += state.level.scrollSpeed * dtSec;
  advanceWindow(state);
  currentController(state).step(state, dtSec);
  if (state.run.phase !== "running") return; // a death inside step() ends the run early
  checkHazards(state);
  if (state.run.phase !== "running") return;
  checkFinish(state);
}

function frame(now) {
  if (last === null) last = now;
  const delta = Math.min(now - last, MAX_FRAME_MS);
  last = now;

  if (state.run.phase === "running") {
    accumulator += delta;
    let steps = 0;
    while (accumulator >= FIXED_DT && steps < MAX_STEPS_PER_FRAME) {
      stepPhysics(FIXED_DT / 1000);
      accumulator -= FIXED_DT;
      state.input.justPressed = false;
      state.input.justReleased = false;
      steps += 1;
      if (state.run.phase !== "running") break;
    }
  } else {
    accumulator = 0;
  }

  updateParticles(delta / 1000);
  render();
  rafHandle = requestAnimationFrame(frame);
}

function startFrameLoop() {
  if (rafHandle !== null) return;
  last = null;
  rafHandle = requestAnimationFrame(frame);
}

function stopFrameLoop() {
  if (rafHandle !== null) cancelAnimationFrame(rafHandle);
  rafHandle = null;
}

// --- Run lifecycle ---------------------------------------------------------

function resetPlayerToStart(lvl) {
  state.player = { mode: lvl.startMode, x: 0, y: 0, vy: 0, angle: 0, grounded: true, gravityDir: 1 };
  lvl.windowStart = 0;
  const ground = groundSurfaceUnderPlayer(state);
  state.player.y = ground !== null ? ground : 0;
}

// The tap that starts or restarts a run also registers as that run's first
// jump — the player is standing still waiting for input, so treating the
// same tap as "go" and "jump" is what the genre trains you to expect.
function beginRun() {
  resetPlayerToStart(state.level);
  state.run.phase = "running";
  state.run.attempts += 1;
  state.input.pressed = true;
  state.input.justPressed = true;
  hideMessage();
  last = null;
  accumulator = 0;
}

function killPlayer() {
  if (state.run.phase !== "running") return;
  state.run.phase = "dead";
  state.run.deaths += 1;
  spawnDeathBurst();
  sfxDeath();
  showMessage("Missed it — tap to retry");
}

function finishLevel() {
  if (state.run.phase !== "running") return;
  state.run.phase = "finished";
  sfxFinish();
  showMessage("Level complete! Tap to play again");
}

// Backgrounding pauses rather than trying to fairly replay a gap of missed
// input — see visibilitychange handler below. Resuming does not also count
// as a jump: unlike starting a (stationary) run, you might be mid-air when
// the tab is hidden, and auto-jumping on return would be a surprise.
function resumeAfterPause() {
  state.run.phase = "running";
  hideMessage();
  last = null;
  accumulator = 0;
  startFrameLoop();
}

// --- Input -------------------------------------------------------------
// AudioContext must be created inside a user-gesture handler for iOS to
// unlock it — this press handler is that gesture, on every press, guarded
// so it only actually constructs the context once.

let audioCtx = null;
function ensureAudioUnlocked() {
  if (audioCtx) {
    if (audioCtx.state === "suspended") audioCtx.resume();
    return;
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  try { audioCtx = new Ctx(); } catch (e) { audioCtx = null; }
}

function beep(freq, ms, when, type) {
  if (!audioCtx) return;
  const start = audioCtx.currentTime + (when || 0);
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = type || "sine";
  osc.frequency.setValueAtTime(freq, start);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(0.2, start + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + ms / 1000);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start(start);
  osc.stop(start + ms / 1000 + 0.02);
}

function sfxJump() { beep(720, 90); }
function sfxDeath() { beep(140, 220, 0, "sawtooth"); }
function sfxFinish() { beep(880, 120); beep(1180, 160, 0.12); }

function onPress() {
  ensureAudioUnlocked();
  const phase = state.run.phase;
  if (phase === "ready" || phase === "dead" || phase === "finished") beginRun();
  else if (phase === "paused") resumeAfterPause();
  else if (phase === "running") { state.input.pressed = true; state.input.justPressed = true; }
}

function onRelease() {
  state.input.pressed = false;
  state.input.justReleased = true;
}

function bindInput() {
  const canvas = document.getElementById("game-canvas");
  canvas.addEventListener("pointerdown", (e) => { e.preventDefault(); onPress(); }, { passive: false });
  window.addEventListener("pointerup", onRelease);
  window.addEventListener("keydown", (e) => {
    if (e.repeat) return;
    if (e.code === "Space" || e.code === "ArrowUp") { e.preventDefault(); onPress(); }
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "Space" || e.code === "ArrowUp") onRelease();
  });
}

// A precision platformer can't fairly continue through a background gap, so
// it pauses outright rather than trying to catch up — see the plan's game
// loop design. Only a running level pauses; menus/death/finished ignore it.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (state.run.phase === "running") {
      state.run.phase = "paused";
      stopFrameLoop();
      showMessage("Paused — tap to resume");
      render();
    }
  }
});

// --- Particles (death burst) --------------------------------------------

function spawnDeathBurst() {
  if (reducedMotion.matches) return;
  const cx = state.player.x, cy = state.player.y + PLAYER_SIZE / 2;
  for (let i = 0; i < 18; i++) {
    state.particles.push({
      x: cx, y: cy,
      vx: (Math.random() - 0.5) * 10,
      vy: Math.random() * 8 + 2,
      life: 1,
      color: i % 2 ? "#ff6b6b" : "#ffd166",
    });
  }
}

function updateParticles(dtSec) {
  const list = state.particles;
  for (let i = list.length - 1; i >= 0; i--) {
    const pt = list[i];
    pt.vy -= 14 * dtSec;
    pt.x += pt.vx * dtSec;
    pt.y += pt.vy * dtSec;
    pt.life -= dtSec * 1.2;
    if (pt.life <= 0) list.splice(i, 1);
  }
}

// --- Rendering -----------------------------------------------------------

function showMessage(text) {
  const el = document.getElementById("hud-message");
  el.textContent = text;
  el.hidden = false;
}

function hideMessage() {
  document.getElementById("hud-message").hidden = true;
}

function updateHud() {
  const pct = state.level
    ? Math.max(0, Math.min(100, Math.round((state.player.x / state.level.lengthUnits) * 100)))
    : 0;
  document.getElementById("hud-pct").textContent = `${pct}%`;
  document.getElementById("hud-attempts").textContent = `Attempt ${Math.max(1, state.run.attempts)}`;
}

function render() {
  const canvas = document.getElementById("game-canvas");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  if (cw === 0 || ch === 0) return;
  const pxW = Math.round(cw * dpr), pxH = Math.round(ch * dpr);
  if (canvas.width !== pxW || canvas.height !== pxH) {
    canvas.width = pxW;
    canvas.height = pxH;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#10131a";
  ctx.fillRect(0, 0, cw, ch);

  if (!state.level) { updateHud(); return; }

  const scale = ch / VIEW_HEIGHT_UNITS;
  const groundScreenY = ch * GROUND_FRACTION;
  const camLeft = state.player.x - (cw / scale) * CAMERA_X_FRACTION;
  const toX = (wx) => (wx - camLeft) * scale;
  const toY = (wy) => groundScreenY - wy * scale;

  ctx.strokeStyle = "rgba(108,140,255,0.08)";
  ctx.lineWidth = 1;
  const firstGridLine = Math.floor(camLeft);
  const lastGridLine = Math.ceil(camLeft + cw / scale);
  for (let wx = firstGridLine; wx <= lastGridLine; wx++) {
    const sx = toX(wx);
    ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, ch); ctx.stroke();
  }

  for (const obj of state.level.objects) {
    if (obj.x + (obj.w || 0) < camLeft - 1 || obj.x > camLeft + cw / scale + 1) continue;
    if (obj.type === "block") {
      const x0 = toX(obj.x), x1 = toX(obj.x + obj.w);
      const y0 = toY(obj.y + obj.h), y1 = toY(obj.y);
      ctx.fillStyle = "#3a4152";
      ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
      ctx.fillStyle = "#4d5670";
      ctx.fillRect(x0, y0, x1 - x0, Math.max(2, (y1 - y0) * 0.12));
    } else if (obj.type === "spike") {
      const x0 = toX(obj.x), x1 = toX(obj.x + obj.w);
      const yBase = toY(obj.y), yTip = toY(obj.y + obj.h);
      ctx.fillStyle = "#e5484d";
      ctx.beginPath();
      ctx.moveTo(x0, yBase); ctx.lineTo(x1, yBase); ctx.lineTo((x0 + x1) / 2, yTip);
      ctx.closePath();
      ctx.fill();
    }
  }

  const p = state.player;
  const half = (PLAYER_SIZE * scale) / 2;
  const px = toX(p.x), py = toY(p.y + PLAYER_SIZE / 2);
  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(p.angle);
  ctx.fillStyle = "#6c8cff";
  ctx.fillRect(-half, -half, half * 2, half * 2);
  ctx.fillStyle = "#10131a";
  ctx.fillRect(-half * 0.35, -half * 0.35, half * 0.5, half * 0.5);
  ctx.restore();

  for (const pt of state.particles) {
    const sx = toX(pt.x), sy = toY(pt.y);
    ctx.globalAlpha = Math.max(0, pt.life);
    ctx.fillStyle = pt.color;
    ctx.fillRect(sx - 3, sy - 3, 6, 6);
    ctx.globalAlpha = 1;
  }

  updateHud();
}

// --- Menu: level list ------------------------------------------------------

async function loadLevelList() {
  try {
    const levels = await fetchJSON("api/levels");
    renderLevelList(levels);
  } catch (e) {
    toast("Couldn't load levels — check your connection.");
  }
}

function renderLevelList(levels) {
  const list = document.getElementById("level-list");
  const empty = document.getElementById("level-list-empty");
  if (!levels.length) {
    list.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  list.innerHTML = levels
    .map(
      (lvl) => `
      <li class="level-card" data-id="${lvl.id}">
        <div class="level-card-info">
          <p class="level-card-name">${escapeHtml(lvl.name)}${lvl.is_official ? '<span class="level-card-official">Official</span>' : ""}</p>
          <p class="level-card-meta">${escapeHtml(lvl.author || "Unknown")}</p>
        </div>
        <button type="button" class="btn-primary level-play-btn" data-id="${lvl.id}">Play</button>
      </li>`
    )
    .join("");
}

document.getElementById("level-list").addEventListener("click", (e) => {
  const btn = e.target.closest(".level-play-btn");
  if (!btn) return;
  openLevel(Number(btn.dataset.id));
});

async function openLevel(id) {
  try {
    const data = await fetchJSON(`api/levels/${id}`);
    startLevel(data);
  } catch (e) {
    toast("Couldn't load that level.");
  }
}

function showScreen(name) {
  document.getElementById("screen-menu").hidden = name !== "menu";
  document.getElementById("screen-play").hidden = name !== "play";
}

function startLevel(levelData) {
  showScreen("play");
  state.level = buildLevelRuntime(levelData);
  state.particles = [];
  state.run = { phase: "ready", attempts: 0, deaths: 0 };
  resetPlayerToStart(state.level);
  showMessage("Tap to start");
  startFrameLoop();
}

document.getElementById("play-back-btn").addEventListener("click", () => {
  stopFrameLoop();
  showScreen("menu");
});

// --- Bootstrap ---------------------------------------------------------

bindInput();
loadLevelList();
