// Copied from PHASES in app/sessions.py - the server owns the order, keep this in step.
const PHASES = ["write", "reveal", "cluster", "vote", "discuss", "done"];

const app = document.getElementById("app");
const live = document.getElementById("live");

let code = (new URLSearchParams(location.search).get("code") || "").toUpperCase();
let state = null; // the last snapshot, replaced wholesale, with deltas applied to it
let status = "connecting";
let socket = null; // set while in the room view, null on the home view
let advancing = false; // an advance request is in flight
let created = false; // this page created `code`, so show the shareable link
let name = localStorage.getItem(key("name")) || "";

function key(k) {
  return `retro:${code}:${k}`;
}

function say(text) {
  live.textContent = text;
}

function el(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

function input(id, label, value, maxLength) {
  const l = el("label", label);
  l.htmlFor = id;
  const i = el("input");
  Object.assign(i, { id, name: id, value, required: true });
  if (maxLength) i.maxLength = maxLength;
  return [l, i];
}

// One JSON POST; null means the fetch itself failed (server down), already announced.
async function post(url, body) {
  try {
    return await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    say("Could not reach the server");
    return null;
  }
}

async function createRetro() {
  const response = await post("/sessions");
  if (!response) return;
  const data = await response.json();
  code = data.code;
  created = true;
  localStorage.setItem(key("facilitator_token"), data.facilitator_token);
  history.replaceState(null, "", `/?code=${code}`);
  render();
}

async function join(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  code = form.get("code").toUpperCase();
  name = form.get("name");
  const response = await post(`/sessions/${code}/join`, { name });
  if (!response) return;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) return say(typeof data.detail === "string" ? data.detail : "Could not join");
  localStorage.setItem(key("participant_token"), data.participant_token);
  localStorage.setItem(key("name"), name);
  history.replaceState(null, "", `/?code=${code}`);
  connect();
}

async function advance() {
  advancing = true;
  render();
  const token = localStorage.getItem(key("facilitator_token"));
  const response = await post(`/sessions/${code}/advance`, { token });
  if (response?.ok) return; // the phase event re-renders; nothing is drawn from this response
  advancing = false;
  if (response) {
    const data = await response.json().catch(() => ({}));
    say(typeof data.detail === "string" ? data.detail : "Could not advance");
  }
  render();
}

function connect() {
  state = null;
  status = "connecting";
  const token = localStorage.getItem(key("participant_token"));
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws/${code}?token=${encodeURIComponent(token)}`);
  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return; // the server never sends a non-JSON frame
    }
    // Later tasks add their cases here: card, card.deleted (#8), cluster (#9),
    // vote (#10), decision, decision.deleted (#11). Until then they are ignored.
    switch (message?.type) {
      case "snapshot":
        state = message;
        status = "connected";
        break;
      case "phase":
        state.phase = message.phase;
        advancing = false;
        break;
      case "error":
        say(message.detail);
        return;
      default:
        return;
    }
    render();
  };
  socket.onclose = (event) => {
    if (event.code === 1008) { // a bad token or an unknown code, always before the snapshot
      for (const k of ["facilitator_token", "participant_token", "name"]) localStorage.removeItem(key(k));
      socket = null;
      render();
      say("Your session was not recognised, join again");
    } else { // reconnect is #12; until then a reload is the way back
      status = "disconnected";
      render();
      say("disconnected");
    }
  };
  render();
}

function renderHome() {
  const create = el("button", "Create a retro");
  create.onclick = createRetro;
  app.append(el("h1", "Weekly Retro"), create);
  if (created) {
    const url = `${location.origin}/?code=${code}`;
    const link = el("a", url);
    link.href = url;
    const share = el("p", `Code ${code}. Share this link: `);
    share.append(link);
    app.append(share);
  }
  const form = el("form");
  form.onsubmit = join;
  form.append(...input("code", "Code", code, 6), ...input("name", "Name", name), el("button", "Join"));
  app.append(form);
}

function renderRoom() {
  const header = el("header");
  header.append(el("span", `Code: ${code}`), el("span", `Phase: ${state ? state.phase : ""}`), el("span", `Status: ${status}`));
  const next = state && PHASES[PHASES.indexOf(state.phase) + 1]; // undefined in done
  if (next && localStorage.getItem(key("facilitator_token"))) {
    const button = el("button", `Next: ${next}`);
    button.disabled = advancing || status !== "connected";
    button.onclick = advance;
    header.append(button);
  }
  // Placeholder body. #8 (write, reveal), #9 (cluster), #10 (vote, discuss) and #11 (done)
  // each replace it for their phase, drawing from `state`.
  const main = el("main");
  if (state) main.append(el("h1", state.phase), el("p", `The retro is in the ${state.phase} phase.`));
  app.append(header, main);
}

// Redraws everything from `state` and the flags above; nothing reads back from the DOM.
function render() {
  app.replaceChildren();
  if (socket) renderRoom();
  else renderHome();
}

if (code && localStorage.getItem(key("participant_token"))) connect();
else render();
