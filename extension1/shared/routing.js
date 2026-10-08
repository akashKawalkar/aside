// shared/routing.js
//
// Where the server's /input route would send a message, worked out locally.
// This mirrors capture/router.py and exists for one purpose: deciding, while
// the server is unreachable, whether a message is safe to hold and send later.

const TRIGGERS = { "note:": "note", "find:": "find", "task:": "task", "schedule:": "schedule", "wrong:": "wrong" };
const MODE_DESTINATIONS = new Set(["task", "schedule", "note"]);

/** "note" | "find" | "task" | "schedule" | "chat", or null for empty input. */
export function localDestination(text, mode) {
  let t = String(text ?? "").trimStart();
  if (!t) return null;

  const byMode = () => (MODE_DESTINATIONS.has(mode) ? mode : "chat");

  // A leading backslash skips trigger detection.
  if (t.startsWith("\\")) return t.slice(1) ? byMode() : null;

  const lowered = t.toLowerCase();
  for (const [prefix, destination] of Object.entries(TRIGGERS)) {
    if (lowered.startsWith(prefix)) return destination;
  }

  return byMode();
}

/**
 * The destination when the message may be queued while offline, else null.
 * Tasks and notes can wait. Schedule entries cannot (the server picks their
 * default time when they are saved, which would be wrong after a delay), and
 * chat needs an answer. A note must have text, because the server refuses
 * an empty one.
 */
export function queueableDestination(text, mode) {
  const destination = localDestination(text, mode);

  if (destination === "task") return "task";

  if (destination === "note") {
    const t = String(text).trimStart();
    const lowered = t.toLowerCase();
    const body = t.startsWith("\\") ? t.slice(1) : lowered.startsWith("note:") ? t.slice(5) : t;
    return body.trim() ? "note" : null;
  }

  return null;
}
