import {
  checkHealth,
  getMonitoring,
  setMonitoring,
  postBrowserEvent,
  sendInput,
} from "./shared/api.js";
import { createQueue } from "./shared/queue.js";
import { createBrowserTracker } from "./shared/browser-tracker.js";

const MONITORING_KEY = "monitoring_enabled"; // cache of the server's state, for the badge and the tracker
const REQUESTED_MODE_KEY = "requested_mode";
const HEALTH_CHECK_ALARM = "health-check";
const HEALTH_CHECK_PERIOD_MINUTES = 1;
const IDLE_SECONDS = 300; // same threshold the collector uses (config.toml idle_threshold)

// ---------- storage ----------

const storage = {
  async get(key, fallback) {
    const stored = await chrome.storage.local.get(key);
    return key in stored ? stored[key] : fallback;
  },
  async set(key, value) {
    await chrome.storage.local.set({ [key]: value });
  },
};

// ---------- side panel behaviour ----------

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  ensureAlarms();
  syncMonitoring();
});

chrome.runtime.onStartup.addListener(() => {
  ensureAlarms();
  syncMonitoring();
  tracker.refresh();
});

function ensureAlarms() {
  chrome.alarms.create(HEALTH_CHECK_ALARM, { periodInMinutes: HEALTH_CHECK_PERIOD_MINUTES });
}

// ---------- monitoring (the server holds the real state; the collector reads it) ----------

async function applyMonitoring(enabled) {
  await storage.set(MONITORING_KEY, enabled);

  // A quiet amber "II" on the toolbar icon while paused; nothing pops up.
  await chrome.action.setBadgeText({ text: enabled ? "" : "II" });
  await chrome.action.setBadgeBackgroundColor({ color: "#fdd663" });

  tracker.refresh();
}

async function syncMonitoring() {
  const response = await getMonitoring();
  if (response.status === "ok") await applyMonitoring(response.data.enabled);
  return response;
}

/**
 * Turn monitoring on or off, or flip it when `enabled` is omitted.
 * Resolves to { enabled } or { error }.
 */
async function changeMonitoring(enabled) {
  let target = enabled;

  if (target === undefined) {
    const current = await getMonitoring();
    if (current.status === "error") return { error: reachMessage(current) };
    target = !current.data.enabled;
  }

  const response = await setMonitoring(target);
  if (response.status === "error") return { error: reachMessage(response) };

  await applyMonitoring(target);
  return { enabled: target };
}

function reachMessage(envelope) {
  return envelope.offline
    ? "Can't reach the local service, so monitoring was not changed."
    : envelope.detail || envelope.message || "Request failed.";
}

// ---------- offline capture queue (tasks and notes only) ----------

async function logDropped(item, envelope) {
  const events = await storage.get("ring_events", []);
  events.unshift({
    ...envelope,
    message: "A queued message was refused when it was sent.",
    detail: `"${item.text.slice(0, 80)}": ${envelope.detail || envelope.message || "refused"}`,
    at: new Date().toISOString(),
  });
  await storage.set("ring_events", events.slice(0, 200));
}

const queue = createQueue({
  storage,
  send: (item) => sendInput(item.text, item.mode, item.key),
  onDrop: logDropped,
});

async function flushQueue() {
  if ((await queue.items()).length === 0) return { sent: 0, dropped: 0, remaining: 0, offline: false };

  const result = await queue.flush();

  if (result.sent > 0) {
    // Tell an open panel so its tiles pick up the new tasks. No panel open is fine.
    chrome.runtime.sendMessage({ type: "queue-flushed", ...result }).catch(() => {});
  }
  return result;
}

// ---------- browser reader ----------

async function readContext() {
  const monitoring = await storage.get(MONITORING_KEY, true);
  if (!monitoring) return { monitoring: false };

  const idleState = await chrome.idle.queryState(IDLE_SECONDS);
  const base = { monitoring: true, idle: idleState !== "active", tab: null };
  if (base.idle) return base;

  let win = null;
  try {
    win = await chrome.windows.getLastFocused({ windowTypes: ["normal"] });
  } catch {
    return base;
  }
  // getLastFocused returns the last window even after focus moved to another app.
  if (!win || !win.focused || win.incognito) return base;

  const [tab] = await chrome.tabs.query({ active: true, windowId: win.id });
  if (tab) base.tab = { url: tab.url, title: tab.title, incognito: tab.incognito };
  return base;
}

const tracker = createBrowserTracker({
  readContext,
  storage,
  send: async (event) => {
    const response = await postBrowserEvent(event);
    if (response.status !== "error") return "sent";
    return response.offline ? "retry" : "drop";
  },
});

// Several events can fire together (a click changes the tab and the focus); look once.
let refreshTimer = null;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => tracker.refresh(), 300);
}

chrome.tabs.onActivated.addListener(refreshSoon);
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url || change.title || change.status === "complete") refreshSoon();
});
chrome.windows.onFocusChanged.addListener(refreshSoon);
chrome.idle.setDetectionInterval(IDLE_SECONDS);
chrome.idle.onStateChanged.addListener(refreshSoon);

// ---------- periodic work ----------

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== HEALTH_CHECK_ALARM) return;

  tracker.tick(); // closes the current stretch so data keeps flowing
  checkAndFlush();
});

async function checkAndFlush() {
  const health = await checkHealth();
  if (health.status === "error") return;

  await syncMonitoring(); // picks up a pause made from somewhere else
  await flushQueue();
}

// ---------- keyboard shortcuts ----------

function openPanelInMode(mode, tab) {
  // Must be the first thing the handler does: opening the panel needs the key press.
  chrome.sidePanel.open({ windowId: tab?.windowId }).catch(() => {});
  // An open panel hears this through storage.onChanged; a panel that is only
  // now loading reads it when it starts.
  storage.set(REQUESTED_MODE_KEY, { mode, at: Date.now() });
}

chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "open-note") openPanelInMode("note", tab);
  else if (command === "open-schedule") openPanelInMode("schedule", tab);
  else if (command === "toggle-monitoring") changeMonitoring();
});

// ---------- messages from the panel and settings page ----------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const respond = (promise) => {
    promise.then(sendResponse, (error) => sendResponse({ error: String(error) }));
    return true; // keep the channel open for the async answer
  };

  switch (message.type) {
    case "toggle-monitoring":
      return respond(changeMonitoring());
    case "set-monitoring":
      return respond(changeMonitoring(Boolean(message.enabled)));
    case "enqueue":
      return respond(queue.enqueue({ mode: message.mode, text: message.text, key: message.key }));
    case "flush-queue":
      return respond(flushQueue());
    default:
      return false;
  }
});

// Try right away on startup, in case items were queued in an earlier session.
checkAndFlush();
tracker.refresh();
