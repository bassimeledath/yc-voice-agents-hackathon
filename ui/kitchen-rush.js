/* Kitchen Rush — game-style replay driven by a continuous shift clock.
 * Data: an array of runs, each with turns[] and final_report.events[].
 * The clock advances in game-seconds; visual state is derived purely from `t`
 * so scrubbing is exact, while one-shot effects are edge-triggered on play. */

// ---- scene anchors (percentages of the 16:9 stage, tuned to kitchen-bg.png) ----
const STATIONS = {
  prep: { x: 20, y: 46, fx: { x: 21, y: 31 } },
  cook: { x: 48.5, y: 46, fx: { x: 48.5, y: 24 } },
  plate: { x: 70, y: 46, fx: { x: 70, y: 28 } },
  check: { x: 31, y: 60, fx: { x: 18, y: 66 } },
  serve: { x: 69, y: 62, fx: { x: 75, y: 76 } },
  center: { x: 48, y: 53, fx: { x: 48, y: 50 } },
  door: { x: 48, y: 90, fx: { x: 48, y: 84 } },
};
const TOP_STATIONS = new Set(["prep", "cook", "plate"]);

// the stove has exactly two burners; active cooks occupy these slots
const MAX_BURNERS = 2;
const BURNER_SLOTS = [
  { x: 45, y: 22 },
  { x: 52.5, y: 22 },
];

const DISH_ICON = { burger: "🍔", soup: "🥣", salad: "🥗" };
const VERB = { prep: "Prep", chop: "Chop", cook: "Cook", plate: "Plate" };
// chop is the salad analog of cook ("ready to plate"); both sit above prepped, below plated
const STATE_RANK = { untouched: 0, prepped: 1, cooking: 2, chopped: 3, cooked: 3, plated: 4, served: 5 };

const ASSETS = { idle: "./assets/chef-idle.png", back: "./assets/chef-back.png" };
const BASE_MS_PER_SEC = 150; // 1 game-second -> 150ms of playback at 1x
const SPEEDS = [1, 2, 0.5];

// ---- DOM ----
const stage = document.querySelector("#stage");
const ticketRail = document.querySelector("#ticketRail");
const cookLayer = document.querySelector("#cookLayer");
const fxLayer = document.querySelector("#fxLayer");
const hudBurners = document.querySelector("#hudBurners");
const burnerCount = document.querySelector("#burnerCount");
const burnerSlots = document.querySelector("#burnerSlots");
const chef = document.querySelector("#chef");
const chefSprite = document.querySelector("#chefSprite");
const chefBubble = document.querySelector("#chefBubble");
const managerBanner = document.querySelector("#managerBanner");
const managerText = document.querySelector("#managerText");
const clockValue = document.querySelector("#clockValue");
const clockMax = document.querySelector("#clockMax");
const resultPill = document.querySelector("#resultPill");
const actionName = document.querySelector("#actionName");
const actionResult = document.querySelector("#actionResult");
const scrubber = document.querySelector("#scrubber");
const verdict = document.querySelector("#verdict");
const verdictCard = document.querySelector("#verdictCard");
const verdictStamp = document.querySelector("#verdictStamp");
const verdictScore = document.querySelector("#verdictScore");
const verdictSub = document.querySelector("#verdictSub");
const runTitle = document.querySelector("#runTitle");
const runSelect = document.querySelector("#runSelect");
const playBtn = document.querySelector("#playBtn");
const restartBtn = document.querySelector("#restartBtn");
const speedBtn = document.querySelector("#speedBtn");
const resultsFile = document.querySelector("#resultsFile");

// ---- runtime ----
const FALLBACK = [{ run_index: 1, scenario: "demo", turns: [], final_report: { won: false, score: 0, max_seconds: 90, tickets: [], events: [] } }];
let runs = Array.isArray(window.KITCHEN_RUSH_SAMPLE) && window.KITCHEN_RUSH_SAMPLE.length ? window.KITCHEN_RUSH_SAMPLE : FALLBACK;
let parsed = null;
let t = 0;
let playing = false;
let speedIdx = 0;
let firedCount = 0; // events whose fx have already played
let lastNow = 0;
let spriteBackOk = false;
let chefStationCurrent = null;
let walkTimer = null;
let lastBubble = "";
let lastManager = "";
const ticketEls = {};
const potEls = {};
const burnerSlotEls = BURNER_SLOTS.map((pos) => {
  const el = document.createElement("div");
  el.className = "burner-slot";
  el.style.left = `${pos.x}%`;
  el.style.top = `${pos.y}%`;
  burnerSlots.appendChild(el);
  return el;
});

// ---- helpers ----
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const itemId = (ticket, dish) => `${String(ticket).toUpperCase()}:${String(dish).toLowerCase()}`;
const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const titleCase = (s) => String(s || "run").replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

function firstSentence(msg) {
  const m = String(msg || "").trim();
  const i = m.indexOf(".");
  return i >= 0 ? m.slice(0, i + 1) : m;
}

function humanizeReason(reason) {
  const map = {
    objective_not_completed: "Objective not completed",
    time_limit_exceeded: "Ran out of time",
    sample_replay: "Shift over",
  };
  if (!reason) return "Shift over";
  if (map[reason]) return map[reason];
  return cap(String(reason).replace(/_/g, " "));
}

function readyAtFromMessage(msg) {
  const m = String(msg || "").match(/ready at (\d+)\s*s/i);
  return m ? Number(m[1]) : null;
}

function stationForTool(tool) {
  const name = tool && tool.name;
  if (name === "serve_dish") return "serve";
  if (name === "check_kitchen") return "check";
  if (name === "start_step") {
    const step = tool.arguments && tool.arguments.step;
    // chopping happens at the prep board, not the stove
    return step === "chop" ? "prep" : step || "prep";
  }
  return "center";
}

// ---- parse a run into a timeline model ----
function parseRun(run) {
  const fr = run.final_report || {};
  const tickets = (fr.tickets || []).map((tk) => ({
    id: tk.id,
    dishes: tk.dishes || [],
    arrival_sec: tk.arrival_sec || 0,
    deadline_sec: tk.deadline_sec || 0,
    priority: tk.priority || null,
  }));

  const events = (fr.events || [])
    .map((e) => ({ ...e, elapsed_sec: e.elapsed_sec || 0 }))
    .sort((a, b) => a.elapsed_sec - b.elapsed_sec);

  const actions = [];
  let prevEnd = 0;
  for (const turn of run.turns || []) {
    const end = Math.max(turn.tool_result ? turn.tool_result.elapsed_sec || 0 : prevEnd, prevEnd);
    const tool = turn.tool_call || {};
    actions.push({
      start: prevEnd,
      end: Math.max(end, prevEnd + 1),
      tool,
      station: stationForTool(tool),
      ok: !(turn.tool_result && turn.tool_result.ok === false),
      message: (turn.tool_result && turn.tool_result.message) || "",
      chefSpeech: turn.chef_speech || null,
      manager: turn.manager || null,
    });
    prevEnd = end;
  }

  const cooks = [];
  for (const e of events) {
    if (e.type === "start_step" && e.detail && e.detail.step === "cook") {
      const ready = readyAtFromMessage(e.message);
      cooks.push({
        ticket: e.detail.ticket,
        dish: e.detail.dish,
        start: e.elapsed_sec,
        ready: ready != null ? ready : e.elapsed_sec + 15,
        id: itemId(e.detail.ticket, e.detail.dish),
      });
    }
  }

  const finalElapsed = Math.max(
    fr.elapsed_sec || 0,
    prevEnd,
    events.length ? events[events.length - 1].elapsed_sec : 0,
    1,
  );

  return {
    tickets,
    events,
    actions,
    cooks,
    finalElapsed,
    maxSeconds: fr.max_seconds || 90,
    won: fr.won === true,
    score: fr.score || 0,
    missedList: fr.missed_deadlines || [],
    lossReason: fr.loss_reason || null,
    scenario: run.scenario || "run",
    runIndex: run.run_index,
  };
}

// ---- derive full visual state at game-time t (pure) ----
function computeStateAt(time) {
  const r = parsed;
  const itemState = {};
  for (const tk of r.tickets) for (const d of tk.dishes) itemState[itemId(tk.id, d)] = "untouched";
  const missed = new Set();
  let ended = false;

  const setState = (id, next) => {
    if (id && STATE_RANK[next] > STATE_RANK[itemState[id] || "untouched"]) itemState[id] = next;
  };

  for (const e of r.events) {
    if (e.elapsed_sec > time) break;
    const d = e.detail || {};
    const id = d.ticket && d.dish ? itemId(d.ticket, d.dish) : null;
    if (e.type === "start_step") {
      if (d.step === "cook") setState(id, "cooking");
      else if (d.step === "chop") setState(id, "chopped");
      else if (d.step === "plate") setState(id, "plated");
      else if (d.step === "prep") setState(id, "prepped");
    } else if (e.type === "cook_complete") setState(id, "cooked");
    else if (e.type === "serve_dish") setState(id, "served");
    else if (e.type === "deadline_missed") {
      missed.add(String(e.message || "").split(" ")[0]);
      ended = true;
    } else if (e.type === "game_complete" || e.type === "time_limit") ended = true;
  }

  const cooking = [];
  for (const c of r.cooks) {
    if (c.start <= time && time < c.ready && itemState[c.id] === "cooking") {
      cooking.push({ ...c, progress: clamp((time - c.start) / Math.max(1, c.ready - c.start), 0, 1) });
    }
  }

  let active = null;
  let lastStarted = null;
  for (const a of r.actions) {
    if (a.start <= time) lastStarted = a;
    if (a.start <= time && time < a.end) active = a;
  }

  const stationName = active ? active.station : lastStarted ? lastStarted.station : "center";
  const pos = STATIONS[stationName] || STATIONS.center;

  let action = null;
  if (active) {
    const step = active.tool.arguments && active.tool.arguments.step;
    let verb = "Act";
    if (active.tool.name === "start_step") verb = VERB[step] || "Step";
    else if (active.tool.name === "serve_dish") verb = "Serve";
    else if (active.tool.name === "check_kitchen") verb = "Check";
    action = { verb, result: firstSentence(active.message) || "…", ok: active.ok };
  }

  const tickets = r.tickets.map((tk) => {
    const arrived = tk.arrival_sec <= time;
    const dishStates = tk.dishes.map((d) => ({ dish: d, state: itemState[itemId(tk.id, d)] }));
    const allServed = dishStates.length > 0 && dishStates.every((d) => d.state === "served");
    const total = Math.max(1, tk.deadline_sec - tk.arrival_sec);
    const remaining = clamp((tk.deadline_sec - time) / total, 0, 1);
    return { ...tk, arrived, dishStates, allServed, isMissed: missed.has(tk.id), remaining };
  });

  const finished = time >= r.finalElapsed;
  return {
    t: time,
    tickets,
    cooking,
    chef: { station: stationName, x: pos.x, y: pos.y, working: !!active },
    chefSpeech: active ? active.chefSpeech : null,
    manager: active ? active.manager : null,
    action,
    ended: ended || finished,
    finished,
  };
}

// ---- render ----
function chefSrcFor(station) {
  return TOP_STATIONS.has(station) && spriteBackOk ? ASSETS.back : ASSETS.idle;
}

function render(s) {
  clockValue.textContent = `${Math.round(s.t)}s`;

  // result pill
  resultPill.classList.toggle("win", s.finished && parsed.won);
  resultPill.classList.toggle("loss", s.finished && !parsed.won);
  resultPill.textContent = s.finished ? (parsed.won ? "Win" : "Loss") : "Live";

  // tickets
  for (const tv of s.tickets) {
    const refs = ticketEls[tv.id];
    if (!refs) continue;
    refs.el.classList.toggle("waiting", !tv.arrived);
    refs.el.classList.toggle("done", tv.allServed);
    refs.el.classList.toggle("missed", tv.isMissed);
    for (const ds of tv.dishStates) {
      const chip = refs.dishEls[ds.dish];
      if (chip) chip.className = `dish ${ds.state}`;
    }
    const pct = tv.allServed ? 100 : Math.round(tv.remaining * 100);
    refs.fill.style.width = `${pct}%`;
    refs.bar.classList.toggle("warn", !tv.allServed && tv.remaining < 0.4 && tv.remaining >= 0.18);
    refs.bar.classList.toggle("danger", !tv.allServed && tv.remaining < 0.18);
  }

  // chef position + facing + gait
  if (!spriteFailed) {
    const src = chefSrcFor(s.chef.station);
    if (!chefSprite.src.endsWith(src.replace("./", ""))) chefSprite.src = src;
  }
  chef.style.left = `${s.chef.x}%`;
  chef.style.top = `${s.chef.y}%`;
  if (s.chef.station !== chefStationCurrent) {
    chefStationCurrent = s.chef.station;
    chef.classList.add("walking");
    chef.classList.remove("working");
    clearTimeout(walkTimer);
    walkTimer = setTimeout(() => {
      chef.classList.remove("walking");
      if (s.chef.working) chef.classList.add("working");
    }, 520);
  } else if (!chef.classList.contains("walking")) {
    chef.classList.toggle("working", s.chef.working);
  }

  // speech
  if (s.chefSpeech) {
    chefBubble.hidden = false;
    if (s.chefSpeech !== lastBubble) {
      chefBubble.textContent = s.chefSpeech;
      restartAnim(chefBubble);
      lastBubble = s.chefSpeech;
    }
  } else {
    chefBubble.hidden = true;
    lastBubble = "";
  }

  if (s.manager) {
    managerBanner.hidden = false;
    if (s.manager !== lastManager) {
      managerText.textContent = s.manager;
      restartAnim(managerBanner);
      lastManager = s.manager;
    }
  } else {
    managerBanner.hidden = true;
    lastManager = "";
  }

  // cooking pots — each active cook occupies one of the two burner slots.
  // Free finished burners first so slot assignment + markers reflect the live set.
  const liveIds = new Set(s.cooking.map((c) => c.id));
  for (const id of Object.keys(potEls)) {
    if (!liveIds.has(id)) {
      potEls[id].remove();
      delete potEls[id];
    }
  }
  const occupied = new Set(Object.values(potEls).map((p) => Number(p.dataset.slot)));
  s.cooking.forEach((c) => {
    let pot = potEls[c.id];
    if (!pot) {
      let slot = 0;
      while (slot < BURNER_SLOTS.length - 1 && occupied.has(slot)) slot += 1;
      occupied.add(slot);
      const pos = BURNER_SLOTS[slot] || BURNER_SLOTS[0];
      pot = document.createElement("div");
      pot.className = "pot";
      pot.dataset.slot = String(slot);
      pot.innerHTML = `<span class="steam">♨</span><span class="pot-ico">${c.dish === "soup" ? "🍲" : "🍳"}</span><span class="pot-sec"></span>`;
      pot.style.left = `${pos.x}%`;
      pot.style.top = `${pos.y}%`;
      cookLayer.appendChild(pot);
      potEls[c.id] = pot;
    }
    pot.style.setProperty("--p", Math.round(c.progress * 100));
    const sec = pot.querySelector(".pot-sec");
    if (sec) sec.textContent = `${Math.max(0, Math.ceil(c.ready - s.t))}s`;
  });

  // burner-capacity HUD + slot markers, from the final live set
  burnerCount.textContent = String(s.cooking.length);
  hudBurners.classList.toggle("full", s.cooking.length >= MAX_BURNERS);
  const liveSlots = new Set(Object.values(potEls).map((p) => Number(p.dataset.slot)));
  for (let i = 0; i < burnerSlotEls.length; i += 1) {
    burnerSlotEls[i].classList.toggle("busy", liveSlots.has(i));
  }

  // action line
  if (s.action) {
    actionName.textContent = s.action.verb;
    actionResult.textContent = s.action.result;
  } else if (s.finished) {
    actionName.textContent = "Done";
    actionResult.textContent = parsed.won
      ? "Shift complete — all tickets served!"
      : humanizeReason(parsed.lossReason);
  } else {
    actionName.textContent = "Ready";
    actionResult.textContent = "Waiting for service…";
  }

  // scrubber
  const pct = clamp((s.t / parsed.finalElapsed) * 100, 0, 100);
  scrubber.value = String(Math.round((s.t / parsed.finalElapsed) * 1000));
  scrubber.style.setProperty("--pct", `${pct}%`);

  // verdict
  if (s.finished) showVerdict();
  else hideVerdict();
}

let spriteFailed = false;
function restartAnim(el) {
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
}

function showVerdict() {
  verdict.hidden = false;
  verdictCard.classList.toggle("win", parsed.won);
  verdictCard.classList.toggle("loss", !parsed.won);
  verdictStamp.textContent = parsed.won ? "Win" : "Loss";
  verdictScore.textContent = `Score ${parsed.score}`;
  verdictSub.textContent = parsed.won
    ? `${titleCase(parsed.scenario)} · ${parsed.finalElapsed}s`
    : parsed.missedList.length
      ? `Missed deadline: ${parsed.missedList.join(", ")}`
      : humanizeReason(parsed.lossReason);
}
function hideVerdict() {
  verdict.hidden = true;
}

// ---- one-shot effects (edge-triggered while playing forward) ----
function fxAt(x, y, glyph, kind) {
  const n = document.createElement("div");
  n.className = `fx ${kind || "float"}`;
  n.textContent = glyph;
  n.style.left = `${x}%`;
  n.style.top = `${y}%`;
  fxLayer.appendChild(n);
  setTimeout(() => n.remove(), 1000);
}
function alertFlash() {
  const n = document.createElement("div");
  n.className = "alert-flash";
  stage.appendChild(n);
  setTimeout(() => n.remove(), 700);
}
function confetti() {
  const colors = ["#f2b134", "#e8743b", "#6fae5f", "#4f8df7", "#e2483a"];
  for (let i = 0; i < 64; i++) {
    const c = document.createElement("div");
    c.className = "confetti";
    c.style.left = `${Math.random() * 100}%`;
    c.style.background = colors[i % colors.length];
    c.style.animationDuration = `${0.9 + Math.random() * 0.9}s`;
    c.style.transform = `rotate(${Math.random() * 360}deg)`;
    stage.appendChild(c);
    setTimeout(() => c.remove(), 2100);
  }
}

function fireEventFx(e) {
  const d = e.detail || {};
  const S = STATIONS;
  switch (e.type) {
    case "ticket_arrived":
      fxAt(S.door.x, S.door.y - 8, "🧾 ORDER!", "burst");
      break;
    case "start_step":
      if (d.step === "prep") fxAt(S.prep.fx.x, S.prep.fx.y, "🥕", "float");
      else if (d.step === "chop") fxAt(S.prep.fx.x, S.prep.fx.y, "🔪", "burst");
      else if (d.step === "cook") fxAt(S.cook.fx.x, S.cook.fx.y, "🔥", "burst");
      else if (d.step === "plate") fxAt(S.plate.fx.x, S.plate.fx.y, "✨", "float");
      break;
    case "cook_complete":
      fxAt(S.cook.fx.x, S.cook.fx.y, "✨", "burst");
      break;
    case "serve_dish":
      fxAt(S.serve.fx.x, S.serve.fx.y, "🔔", "burst");
      break;
    case "check_kitchen":
      fxAt(S.check.fx.x, S.check.fx.y, "📋", "float");
      break;
    case "deadline_missed":
      alertFlash();
      fxAt(50, 42, "⏰ MISSED!", "bad");
      break;
    case "mistake":
    case "unnecessary_tool_call":
      fxAt(S.center.x, S.center.y, "✗", "bad");
      break;
    case "game_complete":
      confetti();
      break;
  }
}

function syncFx(time, allowPlay) {
  if (!allowPlay) {
    let n = 0;
    for (const e of parsed.events) if (e.elapsed_sec <= time) n++;
    firedCount = n;
    return;
  }
  while (firedCount < parsed.events.length && parsed.events[firedCount].elapsed_sec <= time) {
    fireEventFx(parsed.events[firedCount]);
    firedCount += 1;
  }
}

// ---- clock loop ----
function renderTick(allowPlay) {
  render(computeStateAt(t));
  syncFx(t, allowPlay);
}

function frame(now) {
  if (playing) {
    if (!lastNow) lastNow = now;
    const dt = (now - lastNow) / 1000;
    t += dt * SPEEDS[speedIdx] * (1000 / BASE_MS_PER_SEC);
    if (t >= parsed.finalElapsed) {
      t = parsed.finalElapsed;
      playing = false;
      updatePlayBtn();
    }
    renderTick(true);
  }
  lastNow = now;
  requestAnimationFrame(frame);
}

// ---- controls ----
function updatePlayBtn() {
  playBtn.textContent = playing ? "❚❚" : "▶";
}

function setPlaying(next) {
  if (next && t >= parsed.finalElapsed) t = 0; // replay from start
  playing = next;
  lastNow = 0;
  updatePlayBtn();
}

function buildTickets() {
  ticketRail.innerHTML = "";
  for (const k of Object.keys(ticketEls)) delete ticketEls[k];
  for (const tk of parsed.tickets) {
    const el = document.createElement("div");
    el.className = "ticket waiting";
    el.innerHTML = `
      <div class="ticket-head">
        <span class="ticket-id">${tk.id}</span>
        ${tk.priority ? `<span class="ticket-prio">★ ${tk.priority}</span>` : ""}
      </div>
      <div class="ticket-dishes"></div>
      <div class="timebar"><i></i></div>`;
    const dishesWrap = el.querySelector(".ticket-dishes");
    const dishEls = {};
    for (const d of tk.dishes) {
      const chip = document.createElement("span");
      chip.className = "dish";
      chip.innerHTML = `<span class="ico">${DISH_ICON[d] || "🍳"}</span>${d}`;
      dishesWrap.appendChild(chip);
      dishEls[d] = chip;
    }
    ticketRail.appendChild(el);
    ticketEls[tk.id] = {
      el,
      bar: el.querySelector(".timebar"),
      fill: el.querySelector(".timebar > i"),
      dishEls,
    };
  }
}

function setupRun(run) {
  parsed = parseRun(run);
  t = 0;
  firedCount = 0;
  lastNow = 0;
  chefStationCurrent = null;
  lastBubble = "";
  lastManager = "";
  for (const id of Object.keys(potEls)) {
    potEls[id].remove();
    delete potEls[id];
  }
  fxLayer.innerHTML = "";
  runTitle.textContent = titleCase(parsed.scenario);
  clockMax.textContent = `${parsed.maxSeconds}s`;
  buildTickets();
  setPlaying(true);
  renderTick(false);
}

function buildRunSelect() {
  runSelect.innerHTML = "";
  runs.forEach((run, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = `Run ${run.run_index ?? i + 1} · ${titleCase(run.scenario || "run")}`;
    runSelect.appendChild(opt);
  });
}

// ---- events ----
playBtn.addEventListener("click", () => setPlaying(!playing));
restartBtn.addEventListener("click", () => {
  t = 0;
  firedCount = 0;
  hideVerdict();
  setPlaying(true);
  renderTick(false);
});
speedBtn.addEventListener("click", () => {
  speedIdx = (speedIdx + 1) % SPEEDS.length;
  speedBtn.textContent = `${SPEEDS[speedIdx]}×`;
});
scrubber.addEventListener("input", () => {
  playing = false;
  updatePlayBtn();
  t = (Number(scrubber.value) / 1000) * parsed.finalElapsed;
  renderTick(false);
});
runSelect.addEventListener("change", () => {
  setupRun(runs[Number(runSelect.value)] || runs[0]);
});
resultsFile.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    runs = Array.isArray(data) ? data : [data];
    buildRunSelect();
    runSelect.value = "0";
    setupRun(runs[0]);
  } catch (err) {
    alert("Could not parse that JSON file.");
    console.error(err);
  }
});

// chef sprite fallback handling
chefSprite.addEventListener("error", () => {
  if (chefSprite.src.includes("chef-idle")) {
    spriteFailed = true;
    chef.classList.add("no-sprite");
    if (!chef.querySelector(".chef-emoji")) {
      const span = document.createElement("span");
      span.className = "chef-emoji";
      span.textContent = "👨‍🍳";
      chef.insertBefore(span, chefSprite);
    }
  }
});
// preload optional back-facing sprite
const backProbe = new Image();
backProbe.onload = () => {
  spriteBackOk = true;
};
backProbe.src = ASSETS.back;

scrubber.max = "1000";
buildRunSelect();
setupRun(runs[0]);
requestAnimationFrame(frame);

// debug / programmatic hook
window.kitchenRush = {
  loadRuns(next) {
    runs = Array.isArray(next) ? next : [next];
    buildRunSelect();
    setupRun(runs[0]);
  },
  seek(sec) {
    t = clamp(sec, 0, parsed.finalElapsed);
    playing = false;
    updatePlayBtn();
    renderTick(false);
  },
  state: () => computeStateAt(t),
};
