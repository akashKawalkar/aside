// shared/ui/approval-card.js — the "read it, then send it" card for a staged model call. Used for chat replies and for
// schedule drafts, in the side panel and in settings. Nothing is sent until the user presses Approve.
import { h } from "./dom.js";
import { icon } from "./icons.js";

/** The exact messages that will be sent, in order, as one readable block. */
function promptText(preview) {
  const messages = preview.messages ?? [];
  if (!messages.length) return preview.context_text || "(empty)";
  return messages.map((m) => `[${m.role}]\n${m.content}`).join("\n\n");
}

/** Plain-language notes about HOW a reply came back, so a cut-off or substituted answer is never mistaken for a normal one. */
export function replyNotes(data = {}) {
  const notes = [];
  if (data.finish_reason === "length") notes.push("The reply was cut off at the length limit, so it may be incomplete.");
  if (data.fell_back_from) notes.push(`${data.fell_back_from} was busy, so ${data.model} answered instead.`);
  return notes;
}

/**
 * createApprovalCard({ preview, pendingId, approve, reject, onApproved, onFailed, onRejected, onBusy, onEnvelope, title })
 *  - approve(pendingId) / reject(pendingId): the API calls, returning the usual envelope.
 *  - onApproved(envelope, card) / onFailed(envelope, card) / onRejected(envelope, card): replace the card's content.
 *    The defaults show the model's text (or the error) in place.
 *  - onBusy(true|false) and onEnvelope(envelope): optional hooks for the caller's status ring.
 * Returns the card element.
 */
export function createApprovalCard({
  preview, pendingId, approve, reject, onApproved, onFailed, onRejected, onBusy, onEnvelope, title = "Send to the model?",
}) {
  const approveBtn = h("button", { type: "button", class: "approval-btn approve stateful" }, "Approve & send");
  const rejectBtn = h("button", { type: "button", class: "approval-btn reject stateful" }, "Cancel");

  const card = h(
    "div",
    { class: "approval-card" },
    h(
      "div",
      { class: "approval-meta" },
      icon("spark"),
      `${title} ${preview.model} · ~${preview.tokens_estimate || 0} tokens (up to ~$${(preview.cost_estimate_usd || 0).toFixed(4)})`
    ),
    h(
      "details",
      { class: "approval-context-details" },
      h("summary", null, "View the exact prompt"),
      h("pre", { class: "approval-context-pre" }, promptText(preview))
    ),
    h("div", { class: "approval-actions" }, approveBtn, rejectBtn)
  );

  const lock = () => {
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
  };
  const note = (iconName, text) =>
    card.replaceChildren(h("div", { class: "approval-meta" }, icon(iconName), text));

  approveBtn.onclick = async () => {
    lock();
    // Replies take 2-30 s normally but can stall for a minute or more when the service is overloaded (measured), so show
    // the wait and, after a while, say why, instead of a frozen button.
    const started = Date.now();
    const hint = h("div", { class: "approval-meta" }, "");
    const tick = () => {
      const seconds = Math.round((Date.now() - started) / 1000);
      approveBtn.textContent = `Calling model… ${seconds}s`;
      if (seconds >= 15 && !hint.isConnected) {
        hint.textContent = "The service looks busy. If it does not answer in time, a backup model is tried automatically.";
        card.insertBefore(hint, card.querySelector(".approval-actions"));
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    onBusy?.(true);
    const resp = await approve(pendingId);
    clearInterval(timer);
    onBusy?.(false);
    onEnvelope?.(resp);
    if (resp.status === "ok") {
      if (onApproved) onApproved(resp, card);
      else {
        card.replaceChildren(
          h("div", { class: "approval-meta" }, icon("check_circle"), "Approved and executed:"),
          h("div", null, resp.data?.text || "(empty response)"),
          ...replyNotes(resp.data).map((text) => h("div", { class: "approval-meta" }, icon("error"), text))
        );
      }
    } else if (onFailed) {
      onFailed(resp, card);
    } else {
      note("error", `Execution failed: ${resp.message || resp.detail || "Error"}`);
    }
  };

  rejectBtn.onclick = async () => {
    lock();
    rejectBtn.textContent = "Cancelling…";
    const resp = await reject(pendingId);
    onEnvelope?.(resp);
    if (onRejected) onRejected(resp, card);
    else note("error", "Request cancelled by user.");
  };

  return card;
}
