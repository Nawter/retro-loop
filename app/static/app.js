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
// Reconnect schedule (#12): the wait before each attempt doubles from 1 s to a 16 s cap, no jitter, never
// gives up. A snapshot resets it; a page load and a join start it over.
const RETRY_INITIAL = 1000;
const RETRY_CAP = 16000;
let delay = RETRY_INITIAL;
let timer = null; // the pending attempt, cancelled by a wake or an online event
let advancing = false; // an advance request is in flight
let sending = ""; // the type of the card.*/cluster.*/vote.*/decision.* in flight, "" when none: cleared by the next event of its kind, or an error
let editing = null; // { id, text }: the card whose edit form is open, with the text as typed
let editingDecision = null; // { id, kind, text, owner }: the decision whose edit form is open, as typed
let decisionDraft = { kind: "decision", text: "", owner: "" }; // the unsent pad composer
let renaming = null; // { id, name }: the cluster whose rename form is open, with the name as typed
let clusterDraft = ""; // the unsent New cluster name
let moving = null; // id of the card whose move we last sent, until its card echo or an error
let voting = null; // id of the card whose vote.* we last sent, until its vote echo or an error
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

// One card.*, cluster.*, vote.* or decision.* message; nothing is drawn until its echo, only its buttons go disabled.
function send(message) {
  if (socket?.readyState !== WebSocket.OPEN) return offline();
  if (sending) return; // a click on a button the re-render already replaced
  sending = message.type;
  if (sending.startsWith("vote.")) voting = message.id; // what the focus rule and "No votes left" read on the echo
  socket.send(JSON.stringify(message));
  render();
}

// One card.move. Never gated: a second drop before the first echo sends too, and the server's
// last write wins (#5). Nothing is drawn until the echo.
function move(id, cluster_id) {
  if (socket?.readyState !== WebSocket.OPEN) return offline();
  moving = id;
  socket.send(JSON.stringify({ type: "card.move", id, cluster_id }));
}

// The backstop behind the disabled buttons: a click between a close and the re-render, a requestSubmit(),
// the console. Nothing is sent, nothing is queued.
function offline() {
  say("Not connected, try again");
  render();
}

// Opens the socket. A page load or a join starts from nothing; a retry (from the timer or a wake) keeps
// the last snapshot on screen until the new one replaces it.
function connect(retry) {
  clearTimeout(timer); // one socket at a time
  socket?.close(); // a live socket only when called from the console; its close is ignored below
  if (!retry) {
    state = null;
    status = "connecting";
    delay = RETRY_INITIAL;
  }
  const token = localStorage.getItem(key("participant_token"));
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  socket = new WebSocket(`${scheme}//${location.host}/ws/${code}?token=${encodeURIComponent(token)}`);
  const ws = socket; // this attempt, so its close can tell whether it has been replaced
  socket.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return; // the server never sends a non-JSON frame
    }
    switch (message?.type) {
      case "snapshot":
        state = message;
        if (status === "reconnecting") say("Connected"); // a first connect announces nothing
        status = "connected";
        delay = RETRY_INITIAL;
        // A form whose item was deleted while the page was away closes silently, as under decision.deleted
        if (editing && !state.cards.some((c) => c.id === editing.id)) editing = null;
        if (renaming && !state.clusters.some((c) => c.id === renaming.id)) renaming = null;
        if (editingDecision && !state.decisions.some((d) => d.id === editingDecision.id)) editingDecision = null;
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
      case "vote": { // the card's total for everyone, `mine` and `left` for this socket (#6); keys are strings
        const id = String(message.card_id);
        state.votes.counts[id] = message.count;
        state.votes.mine[id] = message.mine;
        state.votes.left = message.left;
        if (voting === message.card_id) { // our echo: Vote again, unless it is now disabled or we removed our last
          if (sending === "vote.cast" && message.left === 0) say("No votes left");
          const unvote = sending === "vote.cast" ? message.left === 0 : message.mine > 0;
          focusId = `${unvote ? "unvote" : "vote"}-${message.card_id}`;
          voting = null;
        }
        if (sending.startsWith("vote.")) sending = "";
        break;
      }
      case "decision": { // the same bytes for the whole room (#5): upsert by id
        const i = state.decisions.findIndex((d) => d.id === message.decision.id);
        if (i < 0) state.decisions.push(message.decision);
        else state.decisions[i] = message.decision;
        if (i < 0 && sending === "decision.create") { // our create echo: the next entry is one keystroke away
          decisionDraft = { kind: "decision", text: "", owner: "" };
          focusId = "decision-text";
        } else if (sending === "decision.edit" && editingDecision?.id === message.decision.id) { // our save echo
          editingDecision = null;
          focusId = `edit-decision-${message.decision.id}`;
        }
        if (sending.startsWith("decision.")) sending = "";
        break;
      }
      case "decision.deleted":
        state.decisions = state.decisions.filter((d) => d.id !== message.id);
        if (editingDecision?.id === message.id) editingDecision = null; // gone under our form: nothing to save, nothing to say
        if (sending === "decision.delete") focusId = "decision-text"; // our echo
        if (sending.startsWith("decision.")) sending = "";
        break;
      case "error":
        say(message.detail);
        if (moving) focusId = `move-${moving}`; // a rejected move: that select again, showing what state holds
        if (voting) focusId = `${sending === "vote.remove" ? "unvote" : "vote"}-${voting}`; // a rejected vote: the button pressed
        if (sending === "decision.create") focusId = "decision-add"; // a rejected create or save: the button pressed, enabled again
        if (sending === "decision.edit") focusId = "edit-decision-save";
        moving = null;
        voting = null;
        sending = "";
        break;
      default:
        return;
    }
    render();
  };
  socket.onclose = (event) => {
    if (ws !== socket) return; // a socket the page already replaced
    sending = ""; // nothing sent on this socket can complete
    advancing = false;
    moving = voting = focusId = null;
    if (event.code === 1008) { // a bad token or an unknown code, always before the snapshot
      for (const k of ["facilitator_token", "participant_token", "name"]) localStorage.removeItem(key(k));
      socket = null;
      render();
      say("Your session was not recognised, join again");
    } else { // any other close is a gap: the board stays, said once, and the next attempt is on the schedule
      if (status !== "reconnecting") say("Reconnecting…");
      status = "reconnecting";
      timer = setTimeout(() => connect(true), delay);
      delay = Math.min(delay * 2, RETRY_CAP);
      render();
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
  const ranked = PHASES.indexOf(state.phase) >= PHASES.indexOf("discuss"); // one list: no column heading above, the cluster alongside
  if (ranked || card.cluster_id != null) li.append(el("p", COLUMNS[card.column])); // in a box the column heading is not above it
  if (ranked) li.append(el("p", state.clusters.find((c) => c.id === card.cluster_id)?.name ?? "No cluster"));
  if (ranked || state.phase === "vote") {
    const votes = state.votes.counts[String(card.id)] ?? 0; // the snapshot's keys are strings (#6); a missing key is 0
    const mine = state.votes.mine[String(card.id)] ?? 0;
    const count = el("p", `${votes} ${votes === 1 ? "vote" : "votes"}${mine ? ` (${mine} yours)` : ""}`);
    count.id = `count-${card.id}`;
    li.append(count);
    if (state.phase === "vote") {
      const vote = el("button", "Vote");
      vote.id = `vote-${card.id}`;
      vote.disabled = sending || status !== "connected" || state.votes.left === 0; // a hint over the server's budget, never a substitute
      vote.onclick = () => send({ type: "vote.cast", id: card.id });
      li.append(vote);
      if (mine) {
        const unvote = el("button", "Remove vote");
        unvote.id = `unvote-${card.id}`;
        unvote.disabled = sending || status !== "connected";
        unvote.onclick = () => send({ type: "vote.remove", id: card.id });
        li.append(unvote);
      }
    }
  }
  if (write && card.mine) {
    const edit = el("button", "Edit");
    edit.id = `edit-${card.id}`;
    edit.disabled = status !== "connected"; // no form whose Save is dead
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
      rename.disabled = status !== "connected";
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

// discuss and done: every card in one list, count descending then id ascending, sorted from state on every render.
function renderRanking() {
  const section = el("section");
  section.className = "ranking";
  const h2 = el("h2", "Ranked by votes");
  h2.id = "ranking-heading";
  section.setAttribute("aria-labelledby", h2.id);
  const list = el("ol");
  list.id = "ranking";
  const count = (c) => state.votes.counts[String(c.id)] ?? 0;
  list.append(...[...state.cards].sort((a, b) => count(b) - count(a) || a.id - b.id).map((c) => renderCard(c, false)));
  section.append(h2, list);
  return section;
}

// A pad form: Kind, Text and Owner with ids `<prefix>-kind/-text/-owner`, bound to `draft` as typed, then `button`.
// Submit copies the fields into `draft` (a value set by script fires no input event) and sends `message` with them,
// `owner` null when the input is empty (#5); nothing is drawn until the echo.
function decisionForm(prefix, draft, message, button) {
  const [kindLabel, kind] = field("select", `${prefix}-kind`, "Kind");
  kind.append(new Option("Decision", "decision"), new Option("Action", "action"));
  kind.value = draft.kind;
  kind.onchange = () => (draft.kind = kind.value);
  const [textLabel, text] = field("textarea", `${prefix}-text`, "Text", { value: draft.text, required: true, maxLength: 500 });
  text.oninput = () => (draft.text = text.value);
  const [ownerLabel, owner] = field("input", `${prefix}-owner`, "Owner (optional)", { value: draft.owner, maxLength: 100 });
  owner.oninput = () => (draft.owner = owner.value);
  button.disabled = sending || status !== "connected";
  const form = el("form");
  form.onsubmit = (event) => {
    event.preventDefault();
    Object.assign(draft, { kind: kind.value, text: text.value, owner: owner.value });
    send({ ...message, kind: kind.value, text: text.value, owner: owner.value || null });
  };
  form.append(kindLabel, kind, textLabel, text, ownerLabel, owner, button);
  return form;
}

// discuss and done: the pad below the ranked list, every entry of state.decisions by id; the composer and
// Edit/Delete in discuss only, since the server closes decision.* in done (#5). Anyone edits or deletes any entry.
function renderDecisions() {
  const edit = state.phase === "discuss";
  const section = el("section");
  section.className = "decisions";
  const h2 = el("h2", "Decisions and actions");
  h2.id = "decisions-heading";
  section.setAttribute("aria-labelledby", h2.id);
  section.append(h2);
  if (edit) {
    const add = el("button", "Add");
    add.id = "decision-add";
    section.append(decisionForm("decision", decisionDraft, { type: "decision.create" }, add));
  }
  const list = el("ul");
  list.id = "decisions";
  for (const d of byId(state.decisions)) {
    const li = el("li");
    li.id = `decision-${d.id}`;
    if (edit && editingDecision?.id === d.id) {
      const save = el("button", "Save");
      save.id = "edit-decision-save";
      const form = decisionForm("edit-decision", editingDecision, { type: "decision.edit", id: d.id }, save);
      const cancel = el("button", "Cancel");
      cancel.type = "button";
      cancel.onclick = () => {
        editingDecision = null;
        focusId = `edit-decision-${d.id}`;
        render();
      };
      form.append(cancel);
      li.append(form);
    } else {
      li.append(el("p", d.kind === "action" ? "Action" : "Decision"), el("p", d.text)); // the tag is the signal, not a colour
      if (d.owner != null) li.append(el("p", `Owner: ${d.owner}`));
      if (edit) {
        const open = el("button", "Edit");
        open.id = `edit-decision-${d.id}`;
        open.disabled = status !== "connected";
        open.onclick = () => {
          editingDecision = { id: d.id, kind: d.kind, text: d.text, owner: d.owner ?? "" }; // one form at a time: this replaces any other
          focusId = "edit-decision-text";
          render();
        };
        const del = el("button", "Delete");
        del.id = `delete-decision-${d.id}`;
        del.disabled = sending || status !== "connected";
        del.onclick = () => send({ type: "decision.delete", id: d.id });
        li.append(open, del);
      }
    }
    list.append(li);
  }
  section.append(list);
  return section;
}

// The whole retro as markdown from `state` and `code`: blocks joined by one blank line, an empty block omitted with
// its heading, one trailing newline. Text goes in verbatim, a newline in it becoming one space so each item is one line.
function markdown() {
  const line = (text) => text.replace(/\r?\n/g, " ");
  const cards = byId(state.cards);
  const blocks = [`# Retro ${code}`];
  for (const [column, heading] of Object.entries(COLUMNS)) {
    const lines = cards.filter((c) => c.column === column).map((c) => {
      const votes = state.votes.counts[String(c.id)] ?? 0; // string keys (#6); never (N yours)
      const cluster = c.cluster_id == null ? "" : ` [${state.clusters.find((k) => k.id === c.cluster_id)?.name}]`;
      return `- ${line(c.text)} (${c.author ?? "Anonymous"}, ${votes} ${votes === 1 ? "vote" : "votes"})${cluster}`;
    });
    if (lines.length) blocks.push([`## ${heading}`, "", ...lines].join("\n"));
  }
  if (state.clusters.length) {
    const lines = byId(state.clusters).flatMap((k) => [`- ${k.name}`, ...cards.filter((c) => c.cluster_id === k.id).map((c) => `  - ${line(c.text)}`)]);
    blocks.push(["## Clusters", "", ...lines].join("\n"));
  }
  for (const [kind, heading] of [["decision", "Decisions"], ["action", "Actions"]]) {
    const lines = byId(state.decisions).filter((d) => d.kind === kind).map((d) => `- ${line(d.text)}${d.owner == null ? "" : ` (owner: ${d.owner})`}`);
    if (lines.length) blocks.push([`## ${heading}`, "", ...lines].join("\n"));
  }
  return blocks.join("\n\n") + "\n";
}

// discuss and done: the markdown in a readonly textarea, rebuilt from state on every render, and a button that copies
// it. The copy wants a user gesture, so writeText is called in the click handler; when the clipboard is missing (no
// secure context) or refuses, the textarea is focused and selected so Ctrl+C finishes the job.
function renderExport() {
  const section = el("section");
  section.className = "export";
  const h2 = el("h2", "Export");
  h2.id = "export-heading";
  section.setAttribute("aria-labelledby", h2.id);
  const copy = el("button", "Copy as Markdown");
  copy.id = "export";
  const [label, text] = field("textarea", "export-text", "Markdown", { value: markdown(), readOnly: true, rows: 8 });
  copy.onclick = () => {
    const fallback = () => {
      say("Copy failed, select the text below");
      const node = document.getElementById("export-text"); // the current one: a render may have replaced `text` by now
      node.focus();
      node.select();
    };
    if (!navigator.clipboard) return fallback();
    navigator.clipboard.writeText(markdown()).then(() => say("Copied"), fallback);
  };
  section.append(h2, copy, label, text);
  return section;
}

function renderRoom() {
  const header = el("header");
  header.append(el("span", `Code: ${code}`), el("span", `Phase: ${state ? state.phase : ""}`), el("span", `Status: ${status}`));
  if (state?.phase === "vote") { // the budget, from state on every render; on screen while main scrolls inside (#8)
    const left = el("span", `Votes left: ${state.votes.left}`);
    left.id = "votes-left";
    header.append(left);
  }
  const next = state && PHASES[PHASES.indexOf(state.phase) + 1]; // undefined in done
  if (next && localStorage.getItem(key("facilitator_token"))) {
    const button = el("button", `Next: ${next}`);
    button.disabled = advancing || status !== "connected";
    button.onclick = advance;
    header.append(button);
  }
  // The board: composers and Edit/Delete in write, read-only in reveal. From cluster on it is grouped:
  // unclustered cards in their columns, boxes below, editable in cluster only (#5's wider server window
  // is a margin). In vote the cards carry the vote controls; in discuss and done the board is one list
  // ranked by votes with the decision pad and the export below it. All from `state`.
  const main = el("main");
  if (state) {
    const write = state.phase === "write";
    main.append(el("h1", state.phase));
    if (PHASES.indexOf(state.phase) >= PHASES.indexOf("discuss")) main.append(renderRanking(), renderDecisions(), renderExport());
    else {
      main.append(...Object.keys(COLUMNS).map((column) => renderColumn(column, write)));
      if (PHASES.indexOf(state.phase) >= PHASES.indexOf("cluster")) main.append(renderClusters(state.phase === "cluster"));
    }
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

// An opened lid or a network coming back retries now rather than up to 16 s from now; the schedule is not reset.
function wake() {
  if (socket && socket.readyState > WebSocket.OPEN) connect(true); // in the room view, neither OPEN nor CONNECTING
}
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && wake());
window.addEventListener("online", wake);

if (code && localStorage.getItem(key("participant_token"))) connect();
else render();
