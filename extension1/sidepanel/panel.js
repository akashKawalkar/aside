// sidepanel/panel.js
import {
  sendInput,
  getTasks,
  getScheduleTile,
  getScheduleDay,
  updateScheduleEntry,
  deleteScheduleEntry,
  completeTask,
  updateTask,
  getMonitoring,
  checkHealth,
  approveLLMCall,
  rejectLLMCall,
} from "../shared/api.js";
import { localDestination, queueableDestination } from "../shared/routing.js";
import { h } from "../shared/ui/dom.js";
import { icon, checkTick } from "../shared/ui/icons.js";
import { installRipple } from "../shared/ui/ripple.js";
import { showSnackbar } from "../shared/ui/snackbar.js";
import { openPopover } from "../shared/ui/popover.js";
import { openDuePicker } from "../shared/ui/datetime-field.js";
import { createEventEditor } from "../shared/ui/event-editor.js";
import { createApprovalCard } from "../shared/ui/approval-card.js";
import { createDraftSection } from "../shared/ui/schedule-draft.js";
import { formatTimeRange, relativeDue, toIsoOffset } from "../shared/ui/datetime-utils.js";

// ---------- element refs ----------

const el = {
  menuButton: document.getElementById("menu-button"),
  pausedChip: document.getElementById("paused-chip"),

  tasksCount: document.getElementById("tasks-count"),
  taskList: document.getElementById("tile-tasks-list"),
  scheduleHeader: document.getElementById("schedule-header"),
  scheduleList: document.getElementById("tile-schedule-list"),

  chatThread: document.getElementById("chat-thread"),

  composer: document.getElementById("composer"),
  modeChips: document.getElementById("mode-chips"),
  modeIndicator: document.getElementById("mode-indicator"),
  inputIcon: document.getElementById("input-icon"),
  input: document.getElementById("composer-input"),
  tabHint: document.getElementById("tab-hint"),
  sendButton: document.getElementById("send-button"),
  ring: document.getElementById("status-ring"),
};

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------- status ring ----------

const RING_TEXT = {
  idle: "Ready",
  spinning: "Working…",
  ok: "Saved",
  ambiguous: "Saved with a default or fallback",
  error: "Something went wrong",
};

let settleTimer = null;

function setRing(state) {
  // state: "idle" | "spinning" | "ok" | "ambiguous" | "error"
  clearTimeout(settleTimer); // a pending "back to idle" must not wipe a newer state
  el.ring.className = `ring-${state}`;
  el.ring.setAttribute("aria-label", RING_TEXT[state] ?? state);
  el.ring.title = RING_TEXT[state] ?? state;
}

// After an ok/ambiguous result is shown briefly, settle back to idle so the
// ring doesn't permanently show the last result.
function settleRingAfter(ms = 1200) {
  clearTimeout(settleTimer);
  settleTimer = setTimeout(() => setRing("idle"), ms);
}

// /input reports a ring colour: green = saved cleanly, yellow = saved with a
// default or fallback. They reuse the ok / ambiguous styles.
function ringFor(envelope) {
  if (envelope.status !== "ok") return envelope.status;
  return envelope.data?.ring === "yellow" ? "ambiguous" : "ok";
}

function applyEnvelopeToRing(envelope) {
  setRing(ringFor(envelope));

  if (envelope.status === "error") {
    showSnackbar(envelope.detail || envelope.message || "Something went wrong.");
  } else {
    settleRingAfter();
  }

  // Full detail is stashed for settings regardless of outcome.
  stashForSettings(envelope);
}

async function stashForSettings(envelope) {
  if (envelope.status === "ok" && !envelope.detail) return; // nothing worth keeping
  try {
    const { events = [] } = (await chrome.storage.local.get("ring_events")) || {};
    events.unshift({ ...envelope, at: new Date().toISOString() });
    await chrome.storage.local.set({ ring_events: events.slice(0, 200) });
  } catch {
    // Best-effort only — losing a settings log entry is not worth surfacing.
  }
}

// ---------- tile state ----------
// All of this resets when the panel closes (the page is destroyed).

const EXPANDED_LIMIT = 5;

let scheduleExpanded = false;
let tasksCache = [];
let taskTileExpanded = false;
let pinnedTaskId = null;
let animateTasks = false; // play the row-in animation on the next task render
let animateSchedule = false;

function emptyRow(iconName, text) {
  return h("li", { class: "tile-empty" }, icon(iconName), text);
}

function renderSkeleton(list, rows = 2) {
  list.replaceChildren(
    ...Array.from({ length: rows }, () =>
      h("li", { class: "tile-skeleton" }, h("span", { class: "skeleton" }), h("span", { class: "skeleton" }))
    )
  );
}

// ---------- tasks tile ----------

async function loadPinnedTask() {
  try {
    const { pinned_task_id = null } = await chrome.storage.local.get("pinned_task_id");
    pinnedTaskId = pinned_task_id;
  } catch {
    // Pinning is a convenience; start unpinned if storage is unavailable.
  }
}

async function setPinnedTask(id) {
  pinnedTaskId = id;
  try {
    await chrome.storage.local.set({ pinned_task_id: id });
  } catch {
    // Best-effort only.
  }
}

async function completeFlow(task, row, checkbox) {
  checkbox.disabled = true;
  row.classList.add("done");

  const envelope = await completeTask(task.id);
  applyEnvelopeToRing(envelope);

  if (envelope.status === "error") {
    checkbox.checked = false;
    checkbox.disabled = false;
    row.classList.remove("done");
    return;
  }

  // A finished task can't stay pinned; the tile moves to the next one.
  if (pinnedTaskId === task.id) await setPinnedTask(null);

  row.style.maxHeight = `${row.offsetHeight}px`;
  requestAnimationFrame(() => row.classList.add("removing"));
  await sleep(280);
  await refreshTiles();
}

async function saveDue(task, date) {
  const envelope = await updateTask(task.id, { due_at: toIsoOffset(date) });
  applyEnvelopeToRing(envelope);
  if (envelope.status !== "error") await refreshTiles();
}

function taskRow(task, lead, index) {
  const due = task.due_at ? new Date(task.due_at) : null;
  const relative = due ? relativeDue(due) : null;

  const checkbox = h("input", { type: "checkbox", "aria-label": `Complete task: ${task.text}` });
  const check = h(
    "label",
    { class: "check stateful", onclick: (e) => e.stopPropagation() },
    checkbox,
    h("span", { class: "box" }, checkTick())
  );

  const chip = h(
    "button",
    {
      type: "button",
      class: `chip small stateful due-chip${relative?.overdue ? " overdue" : ""}`,
      "aria-label": "Change due date",
      onclick: (e) => {
        e.stopPropagation();
        openDuePicker({
          anchor: chip,
          value: due ?? new Date(),
          onSave: (date) => saveDue(task, date),
        });
      },
    },
    icon("schedule"),
    relative ? relative.text : "Add due date"
  );

  const typeChip = h(
    "button",
    {
      type: "button",
      class: "chip small stateful type-chip",
      "aria-label": "Convert task to note",
      onclick: async (e) => {
        e.stopPropagation();
        typeChip.disabled = true;
        await deleteTask(task.id);
        const env = await sendInput(task.text, "note", "enter");
        if (env.status === "error") {
            showSnackbar("Conversion failed.");
        } else {
            showSnackbar("Converted to note.");
        }
        await refreshTiles();
      },
    },
    icon("swap_horiz"),
    "To note"
  );

  const row = h(
    "li",
    {
      class: `task-row stateful${animateTasks ? " enter" : ""}`,
      style: { "--i": String(index) },
      tabindex: "0",
      dataset: { id: String(task.id) },
    },
    check,
    h("div", { class: "task-main" }, h("span", { class: "task-title" }, task.text), h("div", { class: "task-chips", style: "display: flex; gap: 6px; margin-top: 4px;" }, chip, typeChip))
  );

  checkbox.addEventListener("change", () => completeFlow(task, row, checkbox));

  const toggle = async () => {
    if (!taskTileExpanded) {
      taskTileExpanded = true;
      animateTasks = true;
    } else {
      // Choosing a task pins it; choosing the one already on top just collapses.
      if (task !== lead) await setPinnedTask(task.id);
      taskTileExpanded = false;
    }
    renderTaskTile();
  };

  row.addEventListener("click", toggle);
  row.addEventListener("keydown", (e) => {
    if (e.target === row && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      toggle();
    }
  });

  return row;
}

function renderTaskTile() {
  // Untitled tasks have nothing to show; they live in the settings list.
  const named = tasksCache.filter(
    (task) => typeof task.text === "string" && task.text.trim() !== ""
  );

  el.tasksCount.hidden = named.length < 2;
  el.tasksCount.textContent = String(named.length);
  el.taskList.replaceChildren();

  if (named.length === 0) {
    el.taskList.append(emptyRow("task_alt", "....golf?"));
    return;
  }

  // The pinned task leads if it is still pending, otherwise the top task.
  const lead = named.find((task) => task.id === pinnedTaskId) ?? named[0];
  const shown = taskTileExpanded
    ? [lead, ...named.filter((task) => task !== lead)].slice(0, EXPANDED_LIMIT)
    : [lead];

  shown.forEach((task, i) => el.taskList.append(taskRow(task, lead, i)));
  animateTasks = false;
}

// ---------- schedule tile ----------

// Collapsed: the current entry and the next one. Expanded (click the tile's
// header): up to five entries from today that haven't finished yet.
function scheduleRows(collapsedData, todayData) {
  if (!scheduleExpanded) {
    const current = collapsedData?.current ?? [];
    const next = collapsedData?.next ?? null;
    const rows = current.map((entry) => ({ entry, label: "Now" }));
    if (next) rows.push({ entry: next, label: "Next" });
    return rows;
  }

  const now = Date.now();
  return (todayData?.entries ?? [])
    .filter((entry) => new Date(entry.end_at).getTime() > now)
    .slice(0, EXPANDED_LIMIT)
    .map((entry) => ({
      entry,
      label: new Date(entry.start_at).getTime() <= now ? "Now" : "",
    }));
}

function eventRow(entry, label, index) {
  const start = new Date(entry.start_at);
  const end = new Date(entry.end_at);

  const li = h(
    "li",
    {
      class: `event stateful${label === "Now" ? " now" : ""}${animateSchedule ? " enter" : ""}`,
      style: { "--i": String(index) },
      tabindex: "0",
      title: "Click to edit",
    },
    h(
      "div",
      { class: "event-body" },
      h("span", { class: "event-title" }, entry.title),
      h("span", { class: "event-time" }, formatTimeRange(start, end))
    ),
    label ? h("span", { class: `event-badge ${label.toLowerCase()}` }, label) : null
  );

  li.addEventListener("click", () => openScheduleEditor(li, entry));
  li.addEventListener("keydown", (e) => {
    if (e.target === li && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      openScheduleEditor(li, entry);
    }
  });

  return li;
}

function renderScheduleTile(rows) {
  el.scheduleList.replaceChildren();

  if (rows.length === 0) {
    el.scheduleList.append(emptyRow("event", "Nothing next."));
  }

  rows.forEach(({ entry, label }, i) => el.scheduleList.append(eventRow(entry, label, i)));

  if (scheduleExpanded) {
    const link = h(
      "a",
      {
        href: "#",
        onclick: (e) => {
          e.preventDefault();
          openSettings("#schedule");
        },
      },
      "Open full schedule in settings",
      icon("open_in_new")
    );
    el.scheduleList.append(h("li", { class: "tile-link" }, link));
  }

  animateSchedule = false;
}

function toggleScheduleExpanded() {
  scheduleExpanded = !scheduleExpanded;
  animateSchedule = true;
  el.scheduleHeader.setAttribute("aria-expanded", String(scheduleExpanded));
  refreshTiles();
}

el.scheduleHeader.addEventListener("click", toggleScheduleExpanded);
el.scheduleHeader.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    toggleScheduleExpanded();
  }
});

function openScheduleEditor(li, entry) {
  li.className = "event editing";
  li.removeAttribute("title");
  li.removeAttribute("tabindex");
  li.replaceChildren();

  const editor = createEventEditor({
    entry,
    onCancel: () => refreshTiles(),
    onSave: async ({ title, start, end }) => {
      const envelope = await updateScheduleEntry(entry.id, title, toIsoOffset(start), toIsoOffset(end));
      applyEnvelopeToRing(envelope);
      if (envelope.status === "error") return false;
      await refreshTiles();
      return true;
    },
    onDelete: async () => {
      const envelope = await deleteScheduleEntry(entry.id);
      applyEnvelopeToRing(envelope);
      if (envelope.status === "error") return false;
      await refreshTiles();
      return true;
    },
  });

  li.append(editor.el);
  editor.focus();
}

// ---------- refresh ----------

// "Draft tomorrow" lives under the schedule entries. It renders nothing unless it has something to offer.
const draftSection = createDraftSection({
  onScheduleChanged: () => refreshTiles(),
  onBusy: (busy) => busy && setRing("spinning"),
  onEnvelope: applyEnvelopeToRing,
});
document.getElementById("tile-schedule-draft").append(draftSection.el);

let firstLoad = true;

async function refreshTiles() {
  if (firstLoad) {
    renderSkeleton(el.taskList);
    renderSkeleton(el.scheduleList);
  }

  const [tasks, schedule, today] = await Promise.all([
    getTasks(500),
    getScheduleTile(),
    scheduleExpanded ? getScheduleDay() : Promise.resolve(null),
  ]);
  firstLoad = false;

  // ----- tasks -----
  if (tasks.status === "error") {
    tasksCache = [];
    el.tasksCount.hidden = true;
    el.taskList.replaceChildren(emptyRow("error", "Couldn't load tasks."));
  } else {
    tasksCache = tasks.data?.items ?? [];
    renderTaskTile();
  }

  // ----- schedule -----
  if (schedule.status === "error" || today?.status === "error") {
    el.scheduleList.replaceChildren(emptyRow("error", "Couldn't load schedule."));
  } else {
    renderScheduleTile(scheduleRows(schedule.data, today?.data));
  }
  draftSection.refresh();
}

function appendChatTurn(role, content) {
  // role: "user" | "agent" | "error"
  el.chatThread.querySelector(".chat-empty-state")?.remove();

  const bubble = h("div", { class: "chat-turn" }, content);
  const row = h(
    "div",
    { class: `chat-row ${role === "user" ? "user" : "agent"}${role === "error" ? " error" : ""}` },
    role === "user" ? null : h("span", { class: "avatar" }, icon(role === "error" ? "error" : "spark")),
    bubble
  );

  el.chatThread.appendChild(row);
  el.chatThread.scrollTop = el.chatThread.scrollHeight;
  return row;
}

function appendApprovalTurn(preview, pendingId) {
  const card = createApprovalCard({
    preview,
    pendingId,
    approve: approveLLMCall,
    reject: rejectLLMCall,
    title: "Model:",
    onBusy: (busy) => busy && setRing("spinning"),
    onEnvelope: applyEnvelopeToRing,
  });
  appendChatTurn("agent", card);
}

// ---------- mode (sticky until the panel closes) ----------
// The side panel page is destroyed when the panel closes, so a fresh load
// always starts back in chat.

const MODES = [
  { id: "chat", label: "Chat", icon: "chat", placeholder: "Ask me anything…" },
  { id: "task", label: "Task", icon: "task_alt", placeholder: "Add a task…" },
  { id: "schedule", label: "Schedule", icon: "event", placeholder: "Add a schedule entry…" },
  { id: "note", label: "Note", icon: "note", placeholder: "Save a note…" },
];

const chips = new Map();
let currentMode = "chat";

function buildChips() {
  for (const mode of MODES) {
    const chip = h(
      "button",
      {
        type: "button",
        role: "radio",
        class: "mode-chip stateful",
        "aria-checked": "false",
        tabindex: "-1", // Tab belongs to mode switching inside the input
        dataset: { mode: mode.id },
        onclick: () => {
          setMode(mode.id);
          el.input.focus();
        },
      },
      icon(mode.icon),
      mode.label
    );
    chips.set(mode.id, chip);
    el.modeChips.appendChild(chip);
  }
}

function moveIndicator({ animate = true } = {}) {
  const chip = chips.get(currentMode);
  if (!chip) return;

  if (!animate) el.modeIndicator.style.transition = "none";
  el.modeIndicator.style.width = `${chip.offsetWidth}px`;
  el.modeIndicator.style.transform = `translateX(${chip.offsetLeft}px)`;

  if (!animate) {
    void el.modeIndicator.offsetWidth;
    el.modeIndicator.style.transition = "";
  }
}

function applyMode(id, { animate = true } = {}) {
  const mode = MODES.find((m) => m.id === id);
  currentMode = id;
  el.composer.dataset.mode = id;

  chips.forEach((chip, chipId) => chip.setAttribute("aria-checked", String(chipId === id)));
  moveIndicator({ animate });

  el.inputIcon.replaceChildren(icon(mode.icon));

  if (animate) {
    el.input.classList.add("swapping");
    setTimeout(() => {
      el.input.placeholder = mode.placeholder;
      el.input.classList.remove("swapping");
    }, 140);
  } else {
    el.input.placeholder = mode.placeholder;
  }
}

function setMode(id, { fromKeyboard = false } = {}) {
  if (id === currentMode) return;
  applyMode(id);

  if (fromKeyboard) {
    el.tabHint.classList.remove("pulse");
    void el.tabHint.offsetWidth;
    el.tabHint.classList.add("pulse");
  }
}

function cycleMode(step) {
  const i = MODES.findIndex((m) => m.id === currentMode);
  setMode(MODES[(i + step + MODES.length) % MODES.length].id, { fromKeyboard: true });
}

el.modeChips.addEventListener("keydown", (e) => {
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    e.preventDefault();
    cycleMode(e.key === "ArrowRight" ? 1 : -1);
  }
});

// ---------- send ----------

let sending = false;

function updateSendState() {
  el.sendButton.disabled = sending || el.input.value.trim() === "";
}

async function handleSend(key = "enter") {
  const text = el.input.value.trim();
  if (!text || sending) return;

  const mode = currentMode;

  sending = true;
  updateSendState();
  setRing("spinning");

  try {
    let envelope = await sendInput(text, mode, key);

    if (envelope.status === "error" && envelope.offline) {
      if (await holdOffline(text, mode, key)) return;
      envelope = { ...envelope, detail: offlineMessage(text, mode) };
    }

    applyEnvelopeToRing(envelope);

    if (envelope.status === "error") {
      // Keep the text so nothing typed is lost. Only chat-mode failures
      // are also shown in the thread.
      if (mode === "chat") {
        appendChatTurn("error", envelope.message || "Something went wrong.");
      }
      return;
    }

    el.input.value = "";
    const destination = envelope.data?.destination;

    if (destination === "chat_llm" || destination === "chat_script") {
      appendChatTurn("user", text);
      if (envelope.data?.approval_required && envelope.data?.preview) {
        appendApprovalTurn(envelope.data.preview, envelope.data.pending_id);
      } else {
        appendChatTurn(
          "agent",
          envelope.data?.reply ??
            (envelope.data?.fallback
              ? "Chat isn't wired up yet, so I saved that as a note."
              : "(no reply)")
        );
      }
    }
    // Task / schedule / note destinations never enter the thread.

    refreshTiles();

    // The service is reachable, so anything held while it was down can go now.
    Promise.resolve(chrome.runtime.sendMessage({ type: "flush-queue" })).catch(() => {});
  } finally {
    sending = false;
    updateSendState();
  }
}

// ---------- offline handling ----------

function offlineMessage(text, mode) {
  const destination = localDestination(text, mode);
  const what = destination === "schedule" ? "Schedule entries" : "Chat";
  return `Can't reach the local service. ${what} need it to work, so nothing was saved.`;
}

/** Hold a task or note for later. Resolves true when the message was handled. */
async function holdOffline(text, mode, key) {
  if (!queueableDestination(text, mode)) return false;

  let result;
  try {
    result = await chrome.runtime.sendMessage({ type: "enqueue", mode, text, key });
  } catch {
    return false;
  }

  if (!result?.queued) {
    setRing("error");
    showSnackbar(result?.reason === "full" ? "The offline queue is full." : "Couldn't hold that message.");
    return true;
  }

  el.input.value = "";
  updateSendState();
  setRing("ambiguous"); // saved, but not yet where it belongs
  settleRingAfter(2500);
  showSnackbar(`Saved offline. It will be sent when the service is back (${result.count} waiting).`, {
    duration: 6000,
  });
  return true;
}

el.sendButton.addEventListener("click", () => handleSend("enter"));
el.input.addEventListener("input", updateSendState);
el.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    handleSend(e.ctrlKey ? "ctrl_enter" : "enter");
  } else if (e.key === "Tab" && !e.ctrlKey && !e.altKey && !e.metaKey) {
    e.preventDefault();
    cycleMode(e.shiftKey ? -1 : 1);
  }
});

// ---------- top-bar menu ----------

function openSettings(hash = "") {
  chrome.tabs.create({ url: chrome.runtime.getURL(`settings/settings.html${hash}`) });
}

function paintPaused(paused) {
  el.pausedChip.hidden = !paused;
}

async function cachedMonitoring() {
  try {
    const { monitoring_enabled } = await chrome.storage.local.get("monitoring_enabled");
    return monitoring_enabled !== false;
  } catch {
    return true;
  }
}

async function refreshPaused() {
  paintPaused(!(await cachedMonitoring()));

  const response = await getMonitoring();
  if (response.status === "ok") paintPaused(!response.data.enabled);
}

async function toggleMonitoring() {
  let result;
  try {
    result = await chrome.runtime.sendMessage({ type: "toggle-monitoring" });
  } catch {
    result = { error: "Couldn't reach the extension's background worker." };
  }

  if (result?.error) {
    showSnackbar(result.error);
    return;
  }

  if (typeof result?.enabled === "boolean") {
    paintPaused(!result.enabled);
    showSnackbar(result.enabled ? "Monitoring resumed" : "Monitoring paused");
  }
}

let menuPopover = null;

el.menuButton.addEventListener("click", async () => {
  if (menuPopover) {
    menuPopover.close();
    return;
  }

  const monitoringOn = await cachedMonitoring();
  const items = [
    {
      icon: monitoringOn ? "pause" : "play_arrow",
      label: monitoringOn ? "Pause monitoring" : "Resume monitoring",
      run: toggleMonitoring,
    },
    { icon: "visibility", label: "See what changed", run: () => openSettings("#inspect") },
    { icon: "settings", label: "Settings", run: () => openSettings() },
  ];

  const menu = h(
    "div",
    {
      class: "menu",
      role: "menu",
      onkeydown: (e) => {
        const entries = [...menu.querySelectorAll(".menu-item")];
        const i = entries.indexOf(document.activeElement);
        if (e.key === "ArrowDown") {
          e.preventDefault();
          entries[Math.min(entries.length - 1, i + 1)].focus();
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          entries[Math.max(0, i - 1)].focus();
        }
      },
    },
    items.map((item) =>
      h(
        "button",
        {
          type: "button",
          role: "menuitem",
          class: "menu-item stateful",
          onclick: () => {
            menuPopover?.close();
            item.run();
          },
        },
        icon(item.icon),
        item.label
      )
    )
  );

  el.menuButton.setAttribute("aria-expanded", "true");
  menuPopover = openPopover({
    anchor: el.menuButton,
    content: menu,
    align: "end",
    onClose: () => {
      menuPopover = null;
      el.menuButton.setAttribute("aria-expanded", "false");
    },
  });
  menu.firstElementChild.focus();
});

// ---------- init ----------

async function init() {
  installRipple();

  document.querySelectorAll("[data-icon]").forEach((node) => node.append(icon(node.dataset.icon)));
  el.sendButton.append(icon("send"));

  buildChips();
  applyMode("chat", { animate: false });

  // Fonts change chip widths, and the panel can be resized: keep the pill aligned.
  document.fonts?.ready.then(() => moveIndicator({ animate: false }));
  new ResizeObserver(() => moveIndicator({ animate: false })).observe(el.modeChips);

  el.input.focus();
  updateSendState();

  await consumeRequestedMode();
  refreshPaused();

  await loadPinnedTask();
  await refreshTiles();

  const health = await checkHealth();
  if (health.status === "error") {
    showSnackbar("Can't reach the local service. Is it running?", {
      action: "Retry",
      onAction: () => location.reload(),
      duration: 10000,
    });
  }
}

// ---------- messages from the rest of the extension ----------

const REQUESTED_MODE_KEY = "requested_mode";

function applyRequestedMode(request, { animate }) {
  if (!request || !MODES.some((m) => m.id === request.mode)) return;
  if (animate) setMode(request.mode);
  else applyMode(request.mode, { animate: false });
  el.input.focus();
}

/** A shortcut (Alt+Shift+N / S) opened the panel in a particular mode. */
async function consumeRequestedMode() {
  try {
    const { [REQUESTED_MODE_KEY]: request } = await chrome.storage.local.get(REQUESTED_MODE_KEY);
    if (request && Date.now() - request.at < 15_000) applyRequestedMode(request, { animate: false });
    await chrome.storage.local.remove(REQUESTED_MODE_KEY);
  } catch {
    // Shortcuts are a convenience; the panel works without them.
  }
}

chrome.storage.onChanged?.addListener((changes, area) => {
  if (area !== "local") return;

  const request = changes[REQUESTED_MODE_KEY]?.newValue;
  if (request) {
    applyRequestedMode(request, { animate: true });
    chrome.storage.local.remove(REQUESTED_MODE_KEY);
  }

  if (changes.monitoring_enabled) paintPaused(changes.monitoring_enabled.newValue === false);
});

chrome.runtime.onMessage?.addListener((message) => {
  if (message?.type === "queue-flushed") {
    refreshTiles();
    showSnackbar(`${message.sent} saved offline ${message.sent === 1 ? "item was" : "items were"} sent.`);
  }
});

init();
