// settings/tabs/inspect.js — "What changed": the persistent-file diff log, with undo.
// Hidden gesture: press and hold a row to mark that change wrong (it only logs the mark; no visible thumbs).
import { getDiffLog, undoPersistentChange, markWrong } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const diffLogView = $("diff-log-view");
const UNDOABLE = new Set(["add", "edit", "confirm", "retire", "evict"]);
const LONG_PRESS_MS = 600;

const shown = (value) => (typeof value === "string" ? value : JSON.stringify(value));

function markPrompt(entry, card) {
  const reason = h("input", { class: "pf-input", type: "text", maxlength: "1000", placeholder: "Why? (optional)", "aria-label": "Reason" });
  const prompt = h(
    "div",
    { class: "pf-add mark-wrong" },
    h("span", { class: "supporting" }, "Mark this change as wrong?"),
    reason,
    h("button", { type: "button", class: "btn tonal compact stateful", onclick: async () => {
      const envelope = await markWrong(`diff_log:${entry.id}`, reason.value.trim() || null);
      prompt.remove();
      showSnackbar(envelope.status === "error" ? envelopeError(envelope) : "Noted.");
    } }, "Mark"),
    h("button", { type: "button", class: "btn text compact stateful", onclick: () => prompt.remove() }, "Cancel")
  );
  card.append(prompt);
  reason.focus();
}

function onLongPress(card, entry) {
  let timer = null;
  const cancel = () => { clearTimeout(timer); timer = null; };
  card.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button, input") || card.querySelector(".mark-wrong")) return;
    timer = setTimeout(() => { timer = null; markPrompt(entry, card); }, LONG_PRESS_MS);
  });
  for (const type of ["pointerup", "pointerleave", "pointercancel", "pointermove"]) card.addEventListener(type, cancel);
}

function diffCard(entry, i) {
  const lines = [
    ...[].concat(entry.removed ?? []).map((value) => h("div", { class: "removed" }, `− ${shown(value)}`)),
    ...[].concat(entry.added ?? []).map((value) => h("div", { class: "added" }, `+ ${shown(value)}`)),
  ];

  const head = h(
    "div",
    { class: "log-head" },
    h("span", { class: "chip small" }, entry.source_type),
    entry.source_type !== "undo" && entry.source_id ? h("span", {}, entry.source_id) : null,
    h("span", { class: "when" }, new Date(entry.created_at).toLocaleString())
  );

  if (UNDOABLE.has(entry.source_type) && !entry.undone) {
    head.append(h("button", { type: "button", class: "btn text compact stateful", onclick: async (e) => {
      e.currentTarget.disabled = true;
      const envelope = await undoPersistentChange(entry.id);
      if (envelope.status === "error") {
        e.currentTarget.disabled = false;
        showSnackbar(envelopeError(envelope));
        return;
      }
      showSnackbar("Undone.");
      await loadDiffLog({ quiet: true });
    } }, "Undo"));
  } else if (entry.undone) {
    head.append(h("span", { class: "chip small" }, "undone"));
  }

  const card = h("div", { class: `card log-card${entry.undone ? " undone" : ""}`, style: { "--i": String(i) } }, head, h("div", { class: "diff-lines" }, lines));
  onLongPress(card, entry);
  return card;
}

export async function loadDiffLog({ quiet = false } = {}) {
  if (!quiet) showLoading(diffLogView, 3);
  const envelope = await getDiffLog();

  if (envelope.status === "error") {
    diffLogView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const entries = envelope.data.entries ?? [];
  if (entries.length === 0) {
    diffLogView.replaceChildren(emptyState("visibility", "No changes logged yet."));
    return;
  }

  // Newest first: the change you want to undo is usually the last one.
  diffLogView.replaceChildren(...[...entries].reverse().map(diffCard));
}
