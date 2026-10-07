// shared/browser-tracker.js
//
// Turns "which tab is focused right now" snapshots into stretches of activity
// and delivers them to the local service. Pure logic: the browser APIs, the
// clock, storage and the sender are injected, so it is testable on its own.
//
// Rules: record only while a browser window has focus, you are not idle,
// monitoring is on, and the tab is an ordinary http(s) page outside
// incognito. A stretch ends when the site or title changes, and is cut every
// tick so data keeps flowing and a crash loses at most a minute.

const SEGMENT_KEY = "browser_segment";
const OUTBOX_KEY = "browser_outbox";

/** Host name of an http(s) URL, or null for anything else (chrome://, files, ...). */
export function domainOf(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;

    const host = u.hostname.replace(/\.$/, "").toLowerCase();
    if (!host || /[\[\]:]/.test(host)) return null; // empty or IPv6
    return host;
  } catch {
    return null;
  }
}

/**
 * context: { monitoring, idle, tab: { url, title, incognito } | null }
 * Returns { domain, title } when the tab should be recorded, else null.
 */
export function targetFor(context) {
  if (!context || !context.monitoring || context.idle) return null;
  if (!context.tab || context.tab.incognito) return null;

  const domain = domainOf(context.tab.url);
  if (!domain) return null;

  return { domain, title: context.tab.title || null };
}

/**
 * createBrowserTracker({ now, readContext, send, storage, ... })
 *  - readContext(): async, returns the context described above
 *  - send(event): async, resolves "sent" | "retry" | "drop"
 *  - storage: { get(key, fallback), set(key, value) }
 */
export function createBrowserTracker({
  now = Date.now,
  readContext,
  send,
  storage,
  maxGapMs = 150_000, // longer than this between looks = the worker or the PC was asleep
  minMs = 1000,
  outboxMax = 500,
}) {
  let segment; // undefined until loaded, then an object or null
  let queue = Promise.resolve();

  const iso = (ms) => new Date(ms).toISOString();

  async function load() {
    if (segment === undefined) segment = await storage.get(SEGMENT_KEY, null);
  }

  async function finish(finished, endMs) {
    if (endMs - finished.start < minMs) return;

    const outbox = await storage.get(OUTBOX_KEY, []);
    outbox.push({
      domain: finished.domain,
      title: finished.title,
      ts_start: iso(finished.start),
      ts_end: iso(endMs),
    });
    await storage.set(OUTBOX_KEY, outbox.slice(-outboxMax));
  }

  async function flushOutbox() {
    const outbox = await storage.get(OUTBOX_KEY, []);

    while (outbox.length > 0) {
      if ((await send(outbox[0])) === "retry") break;
      outbox.shift(); // sent, or refused for good
    }
    await storage.set(OUTBOX_KEY, outbox);
  }

  async function step({ cut = false } = {}) {
    await load();

    const t = now();
    const context = await readContext();
    const target = targetFor(context);

    if (segment) {
      const stale = t - segment.heartbeat > maxGapMs;
      const same =
        target && target.domain === segment.domain && (target.title ?? null) === (segment.title ?? null);

      if (stale || !same || cut) {
        // After a gap nothing is known about the missing time, so the stretch
        // ends at the last sign of life. Otherwise it ends now, which for idle
        // is the moment it is detected, as in the app timeline.
        await finish(segment, stale ? segment.heartbeat : t);
        segment = null;
      }
    }

    if (!segment && target) segment = { ...target, start: t, heartbeat: t };
    else if (segment) segment.heartbeat = t;

    await storage.set(SEGMENT_KEY, segment);
    await flushOutbox();
  }

  // Calls run one at a time, in order, and one failure never blocks the next.
  const enqueue = (options) => {
    queue = queue.then(() => step(options)).catch((error) => console.warn("browser tracker:", error));
    return queue;
  };

  return {
    /** Something changed (tab, window focus, idle state, monitoring): look again. */
    refresh: () => enqueue(),
    /** Periodic: close the current stretch and start the next one, and retry delivery. */
    tick: () => enqueue({ cut: true }),
  };
}
