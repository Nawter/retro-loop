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
let sending = ""; // the type of the card.*/cluster.* in flight, "" when none: cleared by the next event of its kind, or an error
let editing = null; // { id, text }: the card whose edit form is open, with the text as typed
let renaming = null; // { id, name }: the cluster whose rename form is open, with the name as typed
let clusterDraft = ""; // the unsent New cluster name
let moving = null; // id of the card whose move we last sent, until its card echo or an error
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

function byId(list) {
  return [...list].sort((a, b) => a.id - b.id);
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

// One card.* or cluster.* message; nothing is drawn until its echo, only its buttons go disabled.
function send(message) {
  if (sending) return; // a click on a button the re-render already replaced
  sending = message.type;
  socket.send(JSON.stringify(message));
  render();
}

// One card.move. Never gated: a second drop before the first echo sends too, and the server's
// last write wins (#5). Nothing is drawn until the echo.
function move(id, cluster_id) {
  if (status !== "connected") return;
  moving = id;
  socket.send(JSON.stringify({ type: "card.move", id, cluster_id }));
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
    // Later tasks add their cases here: vote (#10), decision, decision.deleted (#11).
    // Until then they are ignored.
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
        if (moving === message.card.id) { // the echo of our move: the card's select, in its new place
          moving = null;
          focusId = `move-${message.card.id}`;
        }
        if (sending.startsWith("card.")) sending = "";
        break;
      }
      case "card.deleted": {
        const card = state.cards.find((c) => c.id === message.id);
        state.cards = state.cards.filter((c) => c.id !== message.id);
        if (card) focusId = `${card.column}-text`;
        if (editing?.id === message.id) editing = null;
        if (sending.startsWith("card.")) sending = "";
        break;
      }
      case "cluster": {
        const i = state.clusters.findIndex((c) => c.id === message.cluster.id);
        if (i < 0) state.clusters.push(message.cluster);
        else state.clusters[i] = message.cluster;
        if (i < 0 && sending === "cluster.create") { // our create echo: the next cluster is one keystroke away
          clusterDraft = "";
          focusId = "cluster-name";
        } else if (sending === "cluster.rename" && renaming?.id === message.cluster.id) { // our rename echo
          renaming = null;
          focusId = `rename-${message.cluster.id}`;
        }
        if (sending.startsWith("cluster.")) sending = "";
        break;
      }
      case "error":
        say(message.detail);
        if (moving) focusId = `move-${moving}`; // a rejected move: that select again, showing what state holds
        moving = null;
        sending = "";
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
    send({ type: "card.create", column, text: text.value, anonymous: anon.checked });
  };
  form.append(textLabel, text, anonLabel, add);
  return form;
}

// A drop target in cluster: a column (null) or a box (its id). The card id comes from dataTransfer and
// the target from the section itself, so a drop on a child counts and a re-render mid-drag loses nothing.
function droppable(section, cluster_id) {
  section.ondragover = (event) => {
    event.preventDefault(); // what makes this a target; everything else refuses the drop
    section.classList.add("over");
  };
  section.ondragleave = (event) => { // also fires when the pointer crosses a child, hence the check
    if (!section.contains(event.relatedTarget)) section.classList.remove("over");
  };
  section.ondrop = (event) => {
    event.preventDefault();
    section.classList.remove("over");
    const id = Number(event.dataTransfer.getData("text/plain"));
    if (id) move(id, cluster_id);
  };
}

function renderCard(card, write) {
  const li = el("li");
  li.className = card.column; // the accent follows the card into a box
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
      send({ type: "card.edit", id: card.id, text: text.value });
    };
    form.append(label, text, save, cancel);
    li.append(form);
    return li;
  }
  li.append(el("p", card.text), el("p", card.author ?? "Anonymous"));
  if (card.cluster_id != null) li.append(el("p", COLUMNS[card.column])); // in a box the column heading is not above it
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
    del.onclick = () => send({ type: "card.delete", id: card.id });
    li.append(edit, del);
  }
  if (state.phase === "cluster") { // anyone moves any card: by drag, or by the select for keyboards and touch
    if (status === "connected") li.draggable = true;
    li.ondragstart = (event) => event.dataTransfer.setData("text/plain", String(card.id));
    li.ondragend = () => document.querySelector(".over")?.classList.remove("over"); // Escape, or a drop off-target
    const [label, select] = field("select", `move-${card.id}`, "Move to");
    select.append(new Option("No cluster", ""), ...byId(state.clusters).map((c) => new Option(c.name, c.id)));
    select.value = card.cluster_id ?? "";
    select.disabled = status !== "connected";
    select.onchange = () => move(card.id, select.value === "" ? null : Number(select.value)); // the DOM holds strings, #5 wants integers
    li.append(label, select);
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
  list.append(...byId(state.cards.filter((c) => c.column === column && c.cluster_id == null)).map((c) => renderCard(c, write)));
  section.append(list);
  if (state.phase === "cluster") droppable(section, null);
  return section;
}

function renderBox(cluster, edit) {
  const section = el("section");
  section.id = `cluster-${cluster.id}`;
  if (edit && renaming?.id === cluster.id) {
    section.setAttribute("aria-label", cluster.name);
    const [label, text] = input("rename-text", "Cluster name", renaming.name, 100);
    text.oninput = () => (renaming.name = text.value);
    const save = el("button", "Save");
    save.disabled = sending || status !== "connected";
    const cancel = el("button", "Cancel");
    cancel.type = "button";
    cancel.onclick = () => {
      renaming = null;
      focusId = `rename-${cluster.id}`;
      render();
    };
    const form = el("form");
    form.onsubmit = (event) => {
      event.preventDefault();
      renaming.name = text.value;
      send({ type: "cluster.rename", id: cluster.id, name: text.value });
    };
    form.append(label, text, save, cancel);
    section.append(form);
  } else {
    const h3 = el("h3", cluster.name);
    h3.id = `cluster-${cluster.id}-heading`;
    section.setAttribute("aria-labelledby", h3.id);
    section.append(h3);
    if (edit) {
      const rename = el("button", "Rename");
      rename.id = `rename-${cluster.id}`;
      rename.onclick = () => {
        renaming = { id: cluster.id, name: cluster.name }; // one form at a time: this replaces any other
        focusId = "rename-text";
        render();
      };
      section.append(rename);
    }
  }
  const list = el("ul");
  list.append(...byId(state.cards.filter((c) => c.cluster_id === cluster.id)).map((c) => renderCard(c, false)));
  section.append(list);
  if (edit) droppable(section, cluster.id);
  return section;
}

// The grouped board's fourth section: the New cluster form in cluster, then one box per cluster.
function renderClusters(edit) {
  const section = el("section");
  section.className = "clusters";
  const h2 = el("h2", "Clusters");
  h2.id = "clusters-heading";
  section.setAttribute("aria-labelledby", h2.id);
  section.append(h2);
  if (edit) {
    const [label, text] = input("cluster-name", "New cluster", clusterDraft, 100);
    text.oninput = () => (clusterDraft = text.value);
    const add = el("button", "Add cluster");
    add.disabled = sending || status !== "connected";
    const form = el("form");
    form.onsubmit = (event) => {
      event.preventDefault();
      clusterDraft = text.value; // a value set by script fires no input event
      send({ type: "cluster.create", name: text.value });
    };
    form.append(label, text, add);
    section.append(form);
  }
  const boxes = el("div");
  boxes.append(...byId(state.clusters).map((c) => renderBox(c, edit)));
  section.append(boxes);
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
  // The board: composers and Edit/Delete in write, read-only in reveal. From cluster on it is grouped:
  // unclustered cards in their columns, boxes below, editable in cluster only (#5's wider server window
  // is a margin). #10 (vote, discuss) and #11 (discuss, done) build on the grouped board, from `state`.
  const main = el("main");
  if (state) {
    const write = state.phase === "write";
    main.append(el("h1", state.phase), ...Object.keys(COLUMNS).map((column) => renderColumn(column, write)));
    if (PHASES.indexOf(state.phase) >= PHASES.indexOf("cluster")) main.append(renderClusters(state.phase === "cluster"));
  }
  app.append(header, main);
}

// Redraws everything from `state` and the flags above; nothing reads back from the DOM. Focus, and the
// caret of a text input, survive someone else's event by element id; an own completed action sets focusId.
function render() {
  const active = document.activeElement;
  const keep = !focusId && active?.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
  app.replaceChildren();
  if (socket) renderRoom();
  else renderHome();
  const node = document.getElementById(focusId || keep?.id || "");
  node?.focus();
  if (node && keep?.start != null) node.setSelectionRange(keep.start, keep.end);
  focusId = null;
}

if (code && localStorage.getItem(key("participant_token"))) connect();
else render();
