// shared/queue.js
//
// Messages typed while the local service is unreachable, held until it is back.
// Pure logic: storage and the sender are injected, so it is testable on its own.

export const QUEUE_KEY = "capture_queue";
export const MAX_QUEUED = 200;

/**
 * createQueue({ storage, send, onDrop })
 *  - storage: { get(key, fallback), set(key, value) }
 *  - send(item): resolves to a response envelope. An envelope with
 *    status "error" and offline === true means the service could not be reached.
 *  - onDrop(item, envelope): called for an item the server refused.
 */
export function createQueue({ storage, send, onDrop }) {
  let flushing = null;

  const items = () => storage.get(QUEUE_KEY, []);

  async function enqueue({ mode, text, key = "enter" }) {
    const queue = await items();
    if (queue.length >= MAX_QUEUED) return { queued: false, count: queue.length, reason: "full" };

    queue.push({ mode, text, key, queuedAt: new Date().toISOString() });
    await storage.set(QUEUE_KEY, queue);
    return { queued: true, count: queue.length };
  }

  async function runFlush() {
    let sent = 0;
    let dropped = 0;
    let offline = false;
    let queue = await items();

    // Oldest first. Stop at the first unreachable response so order is kept
    // and nothing is lost on a partial outage.
    while (queue.length > 0) {
      const item = queue[0];
      const envelope = await send(item);

      if (envelope.status === "error" && envelope.offline) {
        offline = true;
        break;
      }

      if (envelope.status === "error") {
        // The server answered and refused it. Retrying cannot help, and one
        // bad item must not block the rest.
        dropped += 1;
        await onDrop?.(item, envelope);
      } else {
        sent += 1;
      }

      queue = queue.slice(1);
      await storage.set(QUEUE_KEY, queue);
    }

    return { sent, dropped, remaining: queue.length, offline };
  }

  /** Deliver what can be delivered. Concurrent calls share one run. */
  function flush() {
    if (!flushing) {
      flushing = runFlush().finally(() => {
        flushing = null;
      });
    }
    return flushing;
  }

  return { enqueue, flush, items };
}
