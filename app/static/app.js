// Copied from PHASES in app/sessions.py - the server owns the order, keep this in step.
const PHASES = ["write", "reveal", "cluster", "vote", "discuss", "done"];
// COLUMNS in app/cards.py, with the heading each column shows.
const COLUMNS = { start: "Start", stop: "Stop", continue: "Continue" };

const app = document.getElementById("app");
const live = document.getElementById("live");

let code = (new URLSearchParams(location.search).get("code") || "").toUpperCase();
let state = null; // the last snapshot, replaced wholesale, with deltas applied to it
let status = "connecting";
let socket = null; // set while in the room view, null on the home view
let advancing = false; // an advance request is in flight
let sending = false; // a card.create/edit/delete is in flight, until the next card, card.deleted or error
let editing = null; // { id, text }: the card whose edit form is open, with the text as typed
let focusId = null; // element id to focus after the next render, set by a completed action of our own
const drafts = Object.fromEntries(Object.keys(COLUMNS).map((c) => [c, blank()])); // unsent composer per column
let created = false; // this page created `code`, so show the shareable link
let name = localStorage.getItem(key("name")) || "";

function blank() {
  return { text: "", anonymous: false };
}

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

// A <label for> and the control it labels, as siblings.
function field(tag, id, label, props) {
  const l = el("label", label);
  l.htmlFor = id;
  const f = el(tag);
  Object.assign(f, { id, name: id }, props);
  return [l, f];
}

function input(id, label, value, maxLength) {
  const [l, i] = field("input", id, label, { value, required: true });
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

// One card.* message; nothing is drawn until its echo, only Add/Save/Delete go disabled.
function sendCard(message) {
  if (sending) return; // a click on a button the re-render already replaced
  sending = true;
  socket.send(JSON.stringify(message));
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
    // Later tasks add their cases here: cluster (#9), vote (#10), decision,
    // decision.deleted (#11). Until then they are ignored.
    switch (message?.type) {
      case "snapshot":
        state = message;
        status = "connected";
        break;
      case "phase":
        state.phase = message.phase;
        advancing = false;
        break;
      case "card": {
        const i = state.cards.findIndex((c) => c.id === message.card.id);
        if (i < 0) state.cards.push(message.card);
        else state.cards[i] = message.card;
        if (i < 0 && state.phase === "write") { // a create echo: that composer is done, the next card is one keystroke away
          drafts[message.card.column] = blank();
          focusId = `${message.card.column}-text`;
        } else if (editing?.id === message.card.id) { // a save echo
          editing = null;
          focusId = `edit-${message.card.id}`;
        }
        sending = false;
        break;
      }
      case "card.deleted": {
        const card = state.cards.find((c) => c.id === message.id);
        state.cards = state.cards.filter((c) => c.id !== message.id);
        if (card) focusId = `${card.column}-text`;
        if (editing?.id === message.id) editing = null;
        sending = false;
        break;
      }
      case "error":
        say(message.detail);
        sending = false;
        break;
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

function renderComposer(column) {
  const draft = drafts[column];
  const [textLabel, text] = field("textarea", `${column}-text`, "New card", { value: draft.text, required: true, maxLength: 500 });
  const [anonLabel, anon] = field("input", `${column}-anonymous`, "Post anonymously", { type: "checkbox", checked: draft.anonymous });
  text.oninput = () => (draft.text = text.value);
  anon.onchange = () => (draft.anonymous = anon.checked);
  anonLabel.prepend(anon);
  const add = el("button", "Add");
  add.disabled = sending || status !== "connected";
  const form = el("form");
  form.onsubmit = (event) => {
    event.preventDefault();
    Object.assign(draft, { text: text.value, anonymous: anon.checked }); // a value set by script fires no input event
    sendCard({ type: "card.create", column, text: text.value, anonymous: anon.checked });
  };
  form.append(textLabel, text, anonLabel, add);
  return form;
}

function renderCard(card, write) {
  const li = el("li");
  if (write && editing?.id === card.id) {
    const [label, text] = field("textarea", "edit-text", "Edit card", { value: editing.text, required: true, maxLength: 500 });
    text.oninput = () => (editing.text = text.value);
    const save = el("button", "Save");
    save.disabled = sending || status !== "connected";
    const cancel = el("button", "Cancel");
    cancel.type = "button";
    cancel.onclick = () => {
      editing = null;
      focusId = `edit-${card.id}`;
      render();
    };
    const form = el("form");
    form.onsubmit = (event) => {
      event.preventDefault();
      editing.text = text.value;
      sendCard({ type: "card.edit", id: card.id, text: text.value });
    };
    form.append(label, text, save, cancel);
    li.append(form);
    return li;
  }
  li.append(el("p", card.text), el("p", card.author ?? "Anonymous"));
  if (write && card.mine) {
    const edit = el("button", "Edit");
    edit.id = `edit-${card.id}`;
    edit.onclick = () => {
      editing = { id: card.id, text: card.text };
      focusId = "edit-text";
      render();
    };
    const del = el("button", "Delete");
    del.disabled = sending || status !== "connected";
    del.onclick = () => sendCard({ type: "card.delete", id: card.id });
    li.append(edit, del);
  }
  return li;
}

function renderColumn(column, write) {
  const section = el("section");
  section.className = column;
  const h2 = el("h2", COLUMNS[column]);
  h2.id = `${column}-heading`;
  section.setAttribute("aria-labelledby", h2.id);
  section.append(h2);
  if (write) section.append(renderComposer(column));
  const list = el("ul");
  list.append(...state.cards.filter((c) => c.column === column).sort((a, b) => a.id - b.id).map((c) => renderCard(c, write)));
  section.append(list);
  return section;
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
  // The board: composers and Edit/Delete in write, read-only from reveal on. #9 (cluster),
  // #10 (vote, discuss) and #11 (done) each replace it for their phase, drawing from `state`.
  const main = el("main");
  if (state) {
    const write = state.phase === "write";
    main.append(el("h1", state.phase), ...Object.keys(COLUMNS).map((column) => renderColumn(column, write)));
  }
  app.append(header, main);
}

// Redraws everything from `state` and the flags above; nothing reads back from the DOM.
function render() {
  app.replaceChildren();
  if (socket) renderRoom();
  else renderHome();
  if (focusId) document.getElementById(focusId)?.focus();
  focusId = null;
}

if (code && localStorage.getItem(key("participant_token"))) connect();
else render();
