// shared/api.js
//
// The extension's single entrypoint to the local service.
// Nothing else in the extension calls fetch() directly against localhost.

const BASE_URL = "http://localhost:8787";
const REQUEST_TIMEOUT_MS = 30000;

// A model call can legitimately take a minute or two, so an approved call gets a longer wait than the default.
// The server's own limit ([llm] request_timeout, 120 s) must stay below this, or the panel gives up first while the
// server keeps running (and spending) the call.
const MODEL_CALL_TIMEOUT_MS = 130000;

async function request(path, options = {}) {
  const { timeoutMs = REQUEST_TIMEOUT_MS, ...fetchOptions } = options;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(BASE_URL + path, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
    });

    let body;

    try {
      body = await response.json();
    } catch {
      return envelopeFromFailure(
        `Server returned a response that wasn't valid JSON. HTTP ${response.status}.`
      );
    }

    // Preserve the backend envelope even for HTTP errors.
    if (typeof body?.status === "string") {
      return body;
    }

    return envelopeFromFailure(
      `Server response was missing the expected status field. HTTP ${response.status}.`
    );
  } catch (err) {
    if (err.name === "AbortError") {
      return envelopeFromFailure(
        `Request to ${path} did not complete within ${timeoutMs / 1000}s.`
      );
    }

    // offline: the service could not be reached at all (as opposed to answering
    // with an error), so the request never arrived and is safe to send again.
    return envelopeFromFailure(
      `Could not reach local service: ${err.message}`,
      { offline: true }
    );
  } finally {
    clearTimeout(timeout);
  }
}

function envelopeFromFailure(detail, extra = {}) {
  return {
    status: "error",
    message: "Request failed.",
    detail,
    data: {},
    ...extra,
  };
}


// A thin generic accessor for the settings pages that read a list endpoint directly (skills, statements).
export const api = {
  get: (path) => request(path, { method: "GET" }),
};

// ---------- unified input ----------

export function sendInput(text, mode, key) {
  return request("/input", {
    method: "POST",
    body: JSON.stringify({
      text,
      mode,
      key,
    }),
  });
}


// ---------- tiles ----------

export function getTasks(limit = 5) {
  return request(`/tasks?limit=${limit}`, { method: "GET" });
}
// fields: { text?, due_at? } — due_at is an ISO string with offset, or null
// to reset to the 24h default.
export function updateTask(taskId, fields) {
  return request(`/tasks/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}
export function deleteTask(taskId) {
  return request(`/tasks/${taskId}`, { method: "DELETE" });
}
export function getTaskHistory(limit = 200) {
  return request(`/tasks/history?limit=${limit}`, { method: "GET" });
}
export function completeTask(taskId) {
  return request(`/tasks/${taskId}/complete`, {
    method: "POST",
  });
}
export function getScheduleTile() {
  return request("/schedule/next", { method: "GET" });
}
// date: "YYYY-MM-DD" (local); omit for today.
export function getScheduleDay(date = null) {
  return request(date ? `/schedule?date=${date}` : "/schedule", { method: "GET" });
}
// startAt / endAt must be ISO strings with a UTC offset (e.g. from Date.toISOString()).
export function updateScheduleEntry(id, title, startAt, endAt) {
  return request(`/schedule/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title, start_at: startAt, end_at: endAt }),
  });
}
export function deleteScheduleEntry(id) {
  return request(`/schedule/${id}`, { method: "DELETE" });
}


// ---------- review ----------

// { review: {date, data, text} | null, settings: {...} }
export function getReview() {
  return request("/review", { method: "GET" });
}

// Today so far; nothing is saved or emailed.
export function previewReview() {
  return request("/review/preview", { method: "POST" });
}

// settings: { email_enabled, frequency, recipient, time: "HH:MM" }
export function saveReviewSettings(settings) {
  return request("/settings/review", {
    method: "POST",
    body: JSON.stringify(settings),
  });
}


// ---------- settings ----------

export function getDiffLog() {
  return request("/settings/diff-log", { method: "GET" });
}


// ---------- monitoring and browser activity ----------

export function getMonitoring() {
  return request("/monitoring", { method: "GET" });
}

export function setMonitoring(enabled) {
  return request("/monitoring", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

// event: { domain, title, ts_start, ts_end } (ISO strings with a UTC offset)
export function postBrowserEvent(event) {
  return request("/events/browser", {
    method: "POST",
    body: JSON.stringify(event),
  });
}

// ---------- persistent file ----------

// { sections: {name: {always_on, about, entries}}, tokens, cap_tokens, settle_days }
export function getPersistentFile() {
  return request("/persistent-file", { method: "GET" });
}

// body: { section, text, replaces_id? }
export function addPersistentEntry(body) {
  return request("/persistent-file/entries", { method: "POST", body: JSON.stringify(body) });
}

// body: { text?, section? }
export function editPersistentEntry(id, body) {
  return request(`/persistent-file/entries/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function confirmPersistentEntry(id) {
  return request(`/persistent-file/entries/${id}/confirm`, { method: "POST" });
}

// Retires the entry: out of every prompt, still in the log.
export function retirePersistentEntry(id) {
  return request(`/persistent-file/entries/${id}`, { method: "DELETE" });
}

export function undoPersistentChange(diffId) {
  return request(`/persistent-file/undo/${diffId}`, { method: "POST" });
}

// Hidden gesture: logs that something was wrong, nothing else. target e.g. "diff_log:12".
export function markWrong(target, reason = null) {
  return request("/mark-wrong", { method: "POST", body: JSON.stringify({ target, reason }) });
}

export function getTable(name) {
  return request(`/tables/${encodeURIComponent(name)}`, { method: "GET" });
}


// ---------- data gaps ----------

export function getDataGaps(month = null) {
  return request(`/data-gaps${month ? `?month=${month}` : ""}`, { method: "GET" });
}


// ---------- context viewer ----------

export function getCompiles(limit = 50) {
  return request(`/context/compiles?limit=${limit}`, { method: "GET" });
}

export function getCompile(id) {
  return request(`/context/compiles/${id}`, { method: "GET" });
}

export function replayCompile(id, recipe) {
  return request(`/context/compiles/${id}/replay?recipe=${encodeURIComponent(recipe)}`, { method: "POST" });
}


// ---------- schedule drafts (plan 3.6) ----------
// A model-made draft is only STAGED by generateDraft / reviseDraft: the reply carries a pending_id and a preview, and
// nothing is sent until approveLLMCall(pending_id). The approved reply's data.result.draft is the new draft.

export function getDraftStatus() {
  return request("/schedule/draft/status", { method: "GET" });
}
export function generateDraft(instruction = "") {
  return request("/schedule/draft/generate", { method: "POST", body: JSON.stringify({ instruction }) });
}
export function placeholderDraft() {
  return request("/schedule/draft/placeholder", { method: "POST", body: "{}" });
}
export function reviseDraft(id, instruction) {
  return request(`/schedule/draft/${id}/revise`, { method: "POST", body: JSON.stringify({ instruction }) });
}
// fields: { title?, start_at?, end_at? }; times are ISO strings with a UTC offset.
export function patchDraftEntry(id, index, fields) {
  return request(`/schedule/draft/${id}/entries/${index}`, { method: "PATCH", body: JSON.stringify(fields) });
}
// indexes: array of entry indexes, or null for every pending entry (accept) / the whole draft (discard).
export function acceptDraft(id, indexes = null) {
  return request(`/schedule/draft/${id}/accept`, { method: "POST", body: JSON.stringify({ indexes }) });
}
export function discardDraft(id, indexes = null) {
  return request(`/schedule/draft/${id}/discard`, { method: "POST", body: JSON.stringify({ indexes }) });
}
export function getDrafts(limit = 30) {
  return request(`/schedule/drafts?limit=${limit}`, { method: "GET" });
}
export function compareDraftDay(day) {
  return request(`/schedule/drafts/${encodeURIComponent(day)}/compare`, { method: "GET" });
}

// ---------- llm approval ----------

export function getPendingLLMCalls() {
  return request("/llm/pending", { method: "GET" });
}

export function getPendingLLMCall(id) {
  return request(`/llm/pending/${encodeURIComponent(id)}`, { method: "GET" });
}

export function approveLLMCall(id) {
  return request(`/llm/approve/${encodeURIComponent(id)}`, { method: "POST", timeoutMs: MODEL_CALL_TIMEOUT_MS });
}

export function rejectLLMCall(id, reason = null) {
  return request(`/llm/reject/${encodeURIComponent(id)}`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}


// ---------- health ----------

export function checkHealth() {
  return request("/health", { method: "GET" });
}


// ---------- notes ----------

export function getNotes(limit = 50) {
  return request(`/notes?limit=${limit}`, { method: "GET" });
}

export function updateNote(id, body) {
  return request(`/notes/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function deleteNote(id) {
  return request(`/notes/${id}`, { method: "DELETE" });
}
