const DEFAULT_REPLAY = {
  game: { id: "yc_interview", name: "YC Interview" },
  left: { role: "founder", label: "Founder" },
  right: { role: "interviewer", label: "YC Partner" },
  turns: [
    {
      speaker_role: "interviewer",
      spoken_text: "Welcome. Start simple: who has this problem, and why do they need it now?",
    },
    {
      speaker_role: "founder",
      spoken_text: "Support leaders at fast-growing teams need QA on every call, not a weekly sample.",
    },
    {
      speaker_role: "interviewer",
      spoken_text: "That sounds broad. What is the wedge that gets someone to switch this month?",
    },
    {
      speaker_role: "founder",
      spoken_text: "We flag missed escalation moments within minutes and tie them to concrete coaching clips.",
    },
  ],
};

let replay = DEFAULT_REPLAY;
let currentTurn = 0;
let playing = true;
let timer = null;

const scene = document.querySelector("#scene");
const gameKind = document.querySelector("#gameKind");
const gameTitle = document.querySelector("#gameTitle");
const leftName = document.querySelector("#leftName");
const rightName = document.querySelector("#rightName");
const speakerName = document.querySelector("#speakerName");
const dialogueText = document.querySelector("#dialogueText");
const playToggle = document.querySelector("#playToggle");

function titleize(value) {
  return String(value || "")
    .split(/[_-]/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function normalizeRunJson(data) {
  const leftRole = data.left?.role || data.participants?.left?.role || "founder";
  const rightRole = data.right?.role || data.participants?.right?.role || "interviewer";
  const turns = (data.turns || [])
    .filter((turn) => turn.spoken_text || turn.heard_text || turn.text)
    .map((turn) => ({
      speaker_role: turn.speaker_role || turn.role || turn.speakerRole,
      speaker: turn.speaker || turn.speaker_name,
      spoken_text: turn.spoken_text || turn.text || turn.heard_text,
    }));

  return {
    game: {
      id: data.game?.id || data.game || "yc_interview",
      name: data.game?.name || titleize(data.game?.id || data.game || "yc_interview"),
    },
    left: {
      role: leftRole,
      label: data.left?.label || data.participants?.left?.label || titleize(leftRole),
    },
    right: {
      role: rightRole,
      label: data.right?.label || data.participants?.right?.label || titleize(rightRole),
    },
    turns,
  };
}

function sideForSpeaker(value) {
  const speaker = String(value || "").toLowerCase();
  if (speaker === "left" || speaker === "right") return speaker;

  const leftValues = [replay.left.role, replay.left.label, "candidate", "founder"].map((item) =>
    String(item || "").toLowerCase(),
  );
  const rightValues = [replay.right.role, replay.right.label, "interviewer", "yc partner"].map(
    (item) => String(item || "").toLowerCase(),
  );

  if (leftValues.includes(speaker)) return "left";
  if (rightValues.includes(speaker)) return "right";
  return currentTurn % 2 === 0 ? "right" : "left";
}

function labelForSide(side) {
  return side === "left" ? replay.left.label : replay.right.label;
}

function setSpeaking(side) {
  scene.classList.toggle("speaking-left", side === "left");
  scene.classList.toggle("speaking-right", side === "right");
}

function setDialogue({ side, text }) {
  setSpeaking(side);
  speakerName.textContent = labelForSide(side);
  dialogueText.textContent = text || "";
}

function renderShell() {
  gameKind.textContent = replay.game?.id ? titleize(replay.game.id) : "Synthetic voice game";
  gameTitle.textContent = replay.game?.name || "Voice Game";
  leftName.textContent = replay.left.label;
  rightName.textContent = replay.right.label;
}

function renderTurn() {
  renderShell();
  const turn = replay.turns[currentTurn] || replay.turns[0];
  if (!turn) {
    setDialogue({ side: "right", text: "" });
    return;
  }

  const side = sideForSpeaker(turn.speaker_role || turn.speaker);
  setDialogue({ side, text: turn.spoken_text });
}

function setReplay(nextReplay) {
  replay = nextReplay;
  currentTurn = 0;
  renderTurn();
  startTimer();
}

function step(delta) {
  if (!replay.turns.length) return;
  currentTurn = (currentTurn + delta + replay.turns.length) % replay.turns.length;
  renderTurn();
}

function startTimer() {
  clearInterval(timer);
  if (playing && replay.turns.length > 1) {
    timer = setInterval(() => step(1), 3600);
  }
}

document.querySelector("#prevTurn").addEventListener("click", () => {
  step(-1);
  startTimer();
});

document.querySelector("#nextTurn").addEventListener("click", () => {
  step(1);
  startTimer();
});

playToggle.addEventListener("click", () => {
  playing = !playing;
  playToggle.textContent = playing ? "Pause" : "Play";
  startTimer();
});

document.querySelector("#runFile").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const data = JSON.parse(await file.text());
  setReplay(normalizeRunJson(data));
});

window.hotseatScene = {
  getState: () => ({ replay, currentTurn, playing }),
  normalizeRunJson,
  renderTurn,
  setReplay,
  step,
};

const params = new URLSearchParams(window.location.search);
const runUrl = params.get("run");

if (runUrl) {
  fetch(runUrl)
    .then((response) => response.json())
    .then((data) => setReplay(normalizeRunJson(data)));
}

renderTurn();
startTimer();
