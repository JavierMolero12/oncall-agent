// The chat page: no framework and no build step. It talks to the routes in web/app.py.
"use strict";

// Health label from /api/status -> pill colour, as in the mockup.
const PILL = {
  "Sano": "p-green",
  "Actualización atascada": "p-amber",
  "Con fallos": "p-red",
  "Sin conexión": "p-grey",
};

// How each stage of the recovery watch shows up in the steps panel: [title, colour].
const RECOVERY_STEP = {
  waiting_merge: ["Esperar aprobación", ""],
  merged: ["PR fusionado", "good"],
  synced: ["Comprobar Argo CD (dev)", "good"],
  healthy: ["Comprobar el entorno (dev)", "good"],
  closed: ["PR cerrado", "warn"],
  timeout: ["Tiempo agotado", "bad"],
  error: ["Vigilancia detenida", "bad"],
};

const STATUS_EVERY_MS = 3000;
const SERVER_DOWN = "No puedo hablar con el servidor del agente. ¿Sigue arrancado?";

const chatLog = document.getElementById("chat-log");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const sendButton = document.getElementById("send-button");
const statusList = document.getElementById("status-list");
const stepsPanel = document.getElementById("steps-panel");
const stepsList = document.getElementById("steps-list");
const stepsEmpty = document.getElementById("steps-empty");

let stepNumber = 0; // steps keep counting across questions, as in the mockup
let chatAbort = null; // set while an answer is arriving; "Nueva conversación" aborts it
const watches = new Set(); // open recovery watches, closed by "Nueva conversación"

// -- small helpers --------------------------------------------------------------------------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function scrollToEnd(container) {
  container.scrollTop = container.scrollHeight;
}

function addBubble(className, content) {
  const bubble = el("div", className);
  bubble.append(content);
  chatLog.append(bubble);
  scrollToEnd(chatLog);
  return bubble;
}

function welcome() {
  return document.getElementById("welcome").content.cloneNode(true);
}

// FastAPI sends errors as {"detail": "..."} with the Spanish message.
async function problem(response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON: fall through to the generic message
  }
  return `El servidor ha respondido con un error (${response.status}). Mira la terminal donde lo arrancaste.`;
}

// -- the agent's text: **bold**, `code` and "-" lists ---------------------------------------
// Built from DOM nodes with textContent, never innerHTML, so nothing the model writes can run
// as HTML in the page.

function renderText(text) {
  const fragment = document.createDocumentFragment();
  let paragraph = null;
  let list = null;
  for (const line of text.split("\n")) {
    const item = line.match(/^\s*[-*•]\s+(.*)$/);
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (item) {
      paragraph = null;
      if (!list) fragment.append((list = el("ul")));
      const entry = el("li");
      inline(entry, item[1]);
      list.append(entry);
    } else if (!line.trim()) {
      paragraph = list = null;
    } else if (heading) {
      paragraph = list = null;
      const bold = el("b");
      inline(bold, heading[1]);
      const title = el("p");
      title.append(bold);
      fragment.append(title);
    } else {
      list = null;
      if (paragraph) paragraph.append(el("br"));
      else fragment.append((paragraph = el("p")));
      inline(paragraph, line);
    }
  }
  return fragment;
}

// One pass over the line: `code` is taken as it is, **bold** may contain `code`. A marker with
// no partner stays as plain text.
function inline(parent, text) {
  let plain = "";
  const flush = () => {
    if (plain) parent.append(plain); // a string becomes a text node, never HTML
    plain = "";
  };
  let i = 0;
  while (i < text.length) {
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i + 1) {
        flush();
        parent.append(el("code", null, text.slice(i + 1, end)));
        i = end + 1;
        continue;
      }
    } else if (text.startsWith("**", i)) {
      const end = closingBold(text, i + 2);
      if (end > i + 2) {
        flush();
        const bold = el("b");
        inline(bold, text.slice(i + 2, end));
        parent.append(bold);
        i = end + 2;
        continue;
      }
    }
    plain += text[i];
    i += 1;
  }
  flush();
}

// Where the "**" that closes a bold run starts, skipping `code` inside it; -1 if there is none.
function closingBold(text, from) {
  let i = from;
  while (i < text.length) {
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      i = end === -1 ? i + 1 : end + 1;
    } else if (text.startsWith("**", i)) {
      return i;
    } else {
      i += 1;
    }
  }
  return -1;
}

// -- steps panel ----------------------------------------------------------------------------

function addStep(title, detail) {
  stepsEmpty.hidden = true;
  stepNumber += 1;
  const item = el("li");
  const head = el("div");
  head.append(`${stepNumber} · `, el("b", null, title));
  if (detail) head.append(` (${detail})`);
  const result = el("div", "pending", "…");
  item.append(head, result);
  stepsList.append(item);
  scrollToEnd(stepsPanel);
  return result;
}

function finishStep(result, tone, text) {
  result.className = tone;
  result.textContent = text;
}

// {"environment": "dev", "pod_name": "x"} -> "dev, x". The proposal's text and long values, like
// a whole file, are left out: the proposal itself shows them in the chat.
const HIDDEN_ARGUMENTS = new Set(["new_content", "title", "explanation"]);

function shortArguments(args) {
  return Object.entries(args || {})
    .filter(([key]) => !HIDDEN_ARGUMENTS.has(key))
    .map(([, v]) => v)
    .filter((v) => typeof v === "number" || (typeof v === "string" && v.length <= 60 && !v.includes("\n")))
    .map((v) => (typeof v === "string" && /^[0-9a-f]{40}$/.test(v) ? v.slice(0, 7) : String(v)))
    .join(", ");
}

// -- chat -----------------------------------------------------------------------------------

// The answer is a stream of Server-Sent Events: blocks of "event: name" and "data: {json}"
// lines, separated by a blank line. EventSource only does GET, so this POST is read by hand.
async function* readEvents(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let end;
    while ((end = buffer.indexOf("\n\n")) !== -1) {
      const event = parseEvent(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (event) yield event;
    }
  }
}

function parseEvent(block) {
  let name = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? [name, JSON.parse(data.join("\n"))] : null;
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message || chatAbort) return; // one question at a time, like the server
  chatInput.value = "";
  ask(message);
});

async function ask(message) {
  addBubble("u", message);
  const thinking = addBubble("a thinking", "Pensando…");
  const turn = { steps: new Map(), proposals: [], finished: false };
  const abort = (chatAbort = new AbortController());
  sendButton.disabled = true;
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
      signal: abort.signal,
    });
    const isStream = (response.headers.get("content-type") || "").startsWith("text/event-stream");
    if (!response.ok || !isStream) {
      addBubble("a error", await problem(response));
      return;
    }
    for await (const [name, data] of readEvents(response)) {
      onChatEvent(name, data, turn, thinking);
    }
    if (!turn.finished) {
      addBubble("a error", "La respuesta se ha cortado. Mira la terminal donde arrancaste el servidor.");
    }
  } catch (error) {
    if (error.name !== "AbortError") addBubble("a error", SERVER_DOWN);
  } finally {
    thinking.remove();
    if (!abort.signal.aborted) turn.proposals.forEach(showProposal); // under the answer
    chatAbort = null;
    sendButton.disabled = false;
    chatInput.focus();
  }
}

function onChatEvent(name, data, turn, thinking) {
  switch (name) {
    case "step_started":
      thinking.textContent = `${data.title}…`;
      turn.steps.set(data.index, addStep(data.title, shortArguments(data.arguments)));
      break;
    case "step_finished": {
      const result = turn.steps.get(data.index) || addStep(data.title);
      finishStep(result, data.ok ? "" : "bad", data.summary);
      break;
    }
    case "fix_proposed":
      turn.proposals.push(data);
      break;
    case "assistant_message":
      thinking.remove();
      addBubble("a", renderText(data.text));
      break;
    case "error":
      thinking.remove();
      addBubble("a error", data.message);
      break;
    case "done":
      turn.finished = true;
      break;
  }
}

// -- the fix: diff, button, pull request, recovery ------------------------------------------

function renderDiff(diff) {
  const pre = el("pre", "diff");
  for (const line of diff.replace(/\n$/, "").split("\n")) {
    let kind = "";
    if (line.startsWith("+++") || line.startsWith("---")) kind = "file";
    else if (line.startsWith("+")) kind = "add";
    else if (line.startsWith("-")) kind = "del";
    else if (line.startsWith("@@")) kind = "hunk";
    pre.append(el("span", kind, line || " "));
  }
  return pre;
}

function showProposal(proposal) {
  const box = el("div", "a");
  const title = el("p");
  title.append(el("b", null, "Propuesta: "), proposal.title);
  const where = el("p", "muted");
  where.append("Fichero: ", el("code", null, proposal.file_path));
  const button = el("button", "btn", "Abrir Pull Request con el arreglo");
  button.type = "button";
  button.addEventListener("click", () => openPullRequest(proposal, button));
  box.append(title, el("p", null, proposal.explanation), where, renderDiff(proposal.diff), button);
  chatLog.append(box);
  scrollToEnd(chatLog);
}

async function openPullRequest(proposal, button) {
  button.disabled = true; // a second click would be ignored by the server anyway
  addBubble("u", "Abrir Pull Request");
  const result = addStep("Abrir Pull Request");
  const failed = (message) => {
    finishStep(result, "bad", message);
    addBubble("a error", message);
    button.disabled = false; // it can be tried again, e.g. after fixing the token
  };
  let response;
  try {
    response = await fetch(`/api/proposals/${encodeURIComponent(proposal.id)}/pull-request`, {
      method: "POST",
    });
  } catch {
    failed(SERVER_DOWN);
    return;
  }
  if (!response.ok) {
    failed(await problem(response));
    return;
  }
  const pr = await response.json();
  finishStep(result, "good", `#${pr.number} en ${repoName(pr.url)}`);
  button.textContent = `Pull Request #${pr.number} abierto`;
  showPullRequest(pr, proposal);
  watchRecovery(proposal.id);
}

function showPullRequest(pr, proposal) {
  const bubble = el("div", "a");
  const intro = el("p");
  intro.append("He abierto el Pull Request. ", el("b", null, "No se aplicará hasta que tú lo apruebes."));
  const card = el("div", "pr");
  const folder = proposal.file_path.split("/").slice(0, -1).join("/");
  card.append(
    el("b", null, `#${pr.number} · ${proposal.title}`),
    el("br"),
    el("span", "muted", `${repoName(pr.url)} · ${folder}`),
  );
  bubble.append(intro, card);
  if (pr.url.startsWith("https://github.com/")) { // only ever link to GitHub
    const link = el("a", "btn ghost", "Ver en GitHub");
    link.href = pr.url;
    link.target = "_blank";
    link.rel = "noopener";
    bubble.append(link);
  }
  chatLog.append(bubble);
  scrollToEnd(chatLog);
}

function repoName(url) {
  try {
    return new URL(url).pathname.split("/")[2] || "GitHub";
  } catch {
    return "GitHub";
  }
}

// EventSource is the browser's own SSE client (GET only). When the watch ends the page closes
// it; otherwise the browser would reconnect and start the watch all over again.
function watchRecovery(proposalId) {
  const source = new EventSource(`/api/proposals/${encodeURIComponent(proposalId)}/watch`);
  const seen = new Set(); // after a reconnect the server starts again: skip stages already shown
  watches.add(source);
  const stop = () => {
    source.close();
    watches.delete(source);
  };
  source.addEventListener("recovery", (event) => {
    const update = JSON.parse(event.data);
    if (seen.has(update.stage)) return;
    seen.add(update.stage);
    const [title, tone] = RECOVERY_STEP[update.stage] || [update.stage, ""];
    finishStep(addStep(title), tone, update.message);
    if (update.done) {
      stop();
      addBubble(`a outcome-${tone || "good"}`, update.message);
    }
  });
  source.onerror = () => {
    if (source.readyState !== EventSource.CLOSED) return; // the browser is reconnecting
    stop();
    const message = "He perdido la conexión con el servidor y he dejado de vigilar el PR.";
    finishStep(addStep("Vigilancia detenida"), "bad", message);
  };
}

// -- cluster status, every 3 s --------------------------------------------------------------

function environmentRow(env) {
  const block = el("div");
  const row = el("div", "env");
  row.append(el("span", null, env.namespace), el("span", `pill ${PILL[env.health] || "p-grey"}`, env.health));
  block.append(row, el("p", "sub", env.detail));
  return block;
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { environments } = await response.json();
    statusList.replaceChildren(...environments.map(environmentRow));
  } catch {
    statusList.replaceChildren(
      environmentRow({ namespace: "servidor del agente", health: "Sin conexión", detail: SERVER_DOWN }),
    );
  }
}

async function pollStatus() {
  await refreshStatus();
  setTimeout(pollStatus, STATUS_EVERY_MS); // the next one waits for this one to finish
}

// -- new conversation -----------------------------------------------------------------------

document.getElementById("reset-button").addEventListener("click", async () => {
  chatAbort?.abort();
  watches.forEach((source) => source.close());
  watches.clear();
  try {
    await fetch("/api/conversation/reset", { method: "POST" });
  } catch {
    // the screen is cleared anyway; the next question will show if the server is down
  }
  chatLog.replaceChildren(welcome());
  stepsList.replaceChildren();
  stepsEmpty.hidden = false;
  stepNumber = 0;
  chatInput.focus();
});

chatLog.replaceChildren(welcome());
pollStatus();
chatInput.focus();
