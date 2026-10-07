// shared/ui/schedule-draft.js — "Draft tomorrow": ask for a draft, read the prompt, approve it, then accept / edit /
// discard the proposed blocks one by one or all at once (plan 3.6). One component for the side panel and settings.
//
// It stays out of the way: with nothing to offer it renders nothing at all. A model-made draft is only ever sent after
// the user approves the exact prompt; the rule-based draft makes no model call.
import {
  acceptDraft, approveLLMCall, discardDraft, generateDraft, getDraftStatus, patchDraftEntry, placeholderDraft,
  rejectLLMCall, reviseDraft,
} from "../api.js";
import { createApprovalCard } from "./approval-card.js";
import { formatTimeRange, toIsoOffset } from "./datetime-utils.js";
import { h } from "./dom.js";
import { createEventEditor } from "./event-editor.js";
import { icon } from "./icons.js";
import { showSnackbar } from "./snackbar.js";

/**
 * createDraftSection({ showReasons, onScheduleChanged, onBusy, onEnvelope, onVisible })
 *  - showReasons: show why each block was proposed (settings only; the panel stays quiet).
 *  - onScheduleChanged(): entries were added to the schedule, so the caller should reload its schedule views.
 *  - onBusy(bool) / onEnvelope(envelope): optional hooks for a status ring.
 *  - onVisible(bool): whether the section currently has anything to show.
 * Returns { el, refresh({ force }) }.
 */
export function createDraftSection({ showReasons = false, onScheduleChanged, onBusy, onEnvelope, onVisible } = {}) {
  const el = h("div", { class: "draft-section", hidden: true });
  let view = "empty"; // empty | link | form | approval | draft | revise
  let current = null;

  const fail = (envelope) => showSnackbar(envelope.detail || envelope.message || "Something went wrong.");

  async function call(promise) {
    onBusy?.(true);
    const envelope = await promise;
    onBusy?.(false);
    onEnvelope?.(envelope);
    return envelope;
  }

  function show(nodes, nextView) {
    view = nextView;
    const list = [nodes].flat().filter(Boolean);
    el.replaceChildren(...list);
    el.hidden = list.length === 0;
    onVisible?.(!el.hidden);
    // an active step (form, prompt, draft) may sit below the fold of a scrolling tile area: bring it into view
    if (!["empty", "link"].includes(nextView)) requestAnimationFrame(() => el.scrollIntoView({ block: "nearest" }));
  }

  const button = (cls, label, onclick, extra = {}) =>
    h("button", { type: "button", class: `btn ${cls} compact stateful`, onclick, ...extra }, label);

  // ---------- states ----------

  async function refresh({ force = false } = {}) {
    // Never wipe something the user is in the middle of (a typed instruction, a prompt waiting for approval, an editor).
    if (!force && (["form", "approval", "revise"].includes(view) || el.querySelector(".event.editing"))) return;

    const envelope = await getDraftStatus();
    if (envelope.status !== "ok") return show(null, "empty"); // an older server or none at all: say nothing
    const state = envelope.data;
    if (state.draft) return renderDraft(state.draft);
    if (state.can_generate) return renderLink(state);
    return show(null, "empty");
  }

  function renderLink(state) {
    show(
      h("button", { type: "button", class: "draft-link stateful", onclick: () => renderForm(state) }, icon("spark"), "Draft tomorrow"),
      "link"
    );
  }

  function renderForm(state) {
    const input = h("input", {
      type: "text", maxlength: "500", "aria-label": "Instruction for tomorrow's draft",
      placeholder: "Anything to know about tomorrow? (optional)",
    });
    const go = button("filled", "Prepare draft", null, { disabled: !state.llm_available });
    const rule = button("outlined", "Rule-based draft", null);
    const cancel = button("text", "Cancel", () => refresh({ force: true }));
    const controls = [input, go, rule, cancel];
    const lock = (busy) => controls.forEach((c) => (c.disabled = busy || (c === go && !state.llm_available)));

    go.onclick = async () => {
      lock(true);
      const envelope = await call(generateDraft(input.value.trim()));
      if (envelope.status === "error") {
        fail(envelope);
        return lock(false);
      }
      renderApproval(envelope.data);
    };
    rule.onclick = async () => {
      lock(true);
      const envelope = await call(placeholderDraft());
      if (envelope.status === "error") {
        fail(envelope);
        return lock(false);
      }
      renderDraft(envelope.data.draft);
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !go.disabled) go.click();
      if (e.key === "Escape") cancel.click();
    });

    show(
      h(
        "div",
        { class: "draft-form" },
        input,
        h("div", { class: "draft-actions" }, go, rule, cancel),
        h(
          "div",
          { class: "draft-hint" },
          state.llm_available
            ? "Nothing is sent until you approve the prompt. The rule-based draft makes no model call."
            : "The daily model-call cap is used up. The rule-based draft needs no model call."
        )
      ),
      "form"
    );
    input.focus();
  }

  function renderApproval(data) {
    const card = createApprovalCard({
      preview: data.preview,
      pendingId: data.pending_id,
      approve: approveLLMCall,
      reject: rejectLLMCall,
      title: "Draft with",
      onBusy,
      onEnvelope,
      onApproved: (resp) => {
        const draft = resp.data?.result?.draft;
        if (draft) renderDraft(draft);
        else refresh({ force: true });
      },
      onFailed: (resp, node) =>
        node.replaceChildren(
          h("div", { class: "approval-meta" }, icon("error"), resp.message || resp.detail || "That did not work."),
          h("div", { class: "approval-actions" }, button("text", "Back", () => refresh({ force: true })))
        ),
      onRejected: () => refresh({ force: true }),
    });
    show(card, "approval");
  }

  // ---------- an open draft ----------

  function afterChange(draft) {
    if (draft.status === "open") renderDraft(draft);
    else refresh({ force: true }); // everything was settled: the schedule itself now shows the result
  }

  async function accept(indexes) {
    const envelope = await call(acceptDraft(current.id, indexes));
    if (envelope.status === "error") return fail(envelope);
    if (envelope.data.conflicts?.length) showSnackbar(envelope.message);
    if (envelope.data.accepted?.length) onScheduleChanged?.();
    afterChange(envelope.data.draft);
  }

  async function discard(indexes) {
    const envelope = await call(discardDraft(current.id, indexes));
    if (envelope.status === "error") return fail(envelope);
    afterChange(envelope.data.draft);
  }

  function iconButton(name, label, onclick) {
    return h(
      "button",
      {
        type: "button", class: "icon-btn small stateful", "aria-label": label, title: label,
        onclick: (e) => {
          e.stopPropagation();
          onclick();
        },
      },
      icon(name)
    );
  }

  function openEditor(li, entry) {
    li.className = "event editing";
    li.removeAttribute("title");
    li.removeAttribute("tabindex");
    li.replaceChildren();

    const editor = createEventEditor({
      entry,
      onCancel: () => renderDraft(current),
      onSave: async ({ title, start, end }) => {
        const envelope = await call(patchDraftEntry(current.id, entry.index, { title, start_at: toIsoOffset(start), end_at: toIsoOffset(end) }));
        if (envelope.status === "error") {
          fail(envelope);
          return false; // keep the editor open so nothing typed is lost
        }
        renderDraft(envelope.data.draft);
        return true;
      },
      onDelete: async () => {
        const envelope = await call(discardDraft(current.id, [entry.index]));
        if (envelope.status === "error") {
          fail(envelope);
          return false;
        }
        afterChange(envelope.data.draft);
        return true;
      },
    });
    li.append(editor.el);
    editor.focus();
  }

  function entryRow(entry, i) {
    const accepted = entry.state === "accepted";
    const li = h(
      "li",
      {
        class: `event draft-entry${accepted ? " accepted" : ""}`, style: { "--i": String(i) },
        tabindex: accepted ? null : "0", title: accepted ? "Added to your schedule" : "Click to edit",
        dataset: { index: String(entry.index) },
      },
      h(
        "div",
        { class: "event-body" },
        h("span", { class: "event-title" }, entry.title),
        h("span", { class: "event-time" }, formatTimeRange(new Date(entry.start_at), new Date(entry.end_at))),
        showReasons && entry.reason ? h("span", { class: "event-reason" }, entry.reason) : null
      ),
      accepted
        ? h("span", { class: "draft-badge" }, icon("check"), "Added")
        : h("div", { class: "draft-entry-actions" }, iconButton("check", "Add to my schedule", () => accept([entry.index])), iconButton("close", "Discard", () => discard([entry.index])))
    );
    if (!accepted) {
      li.addEventListener("click", () => openEditor(li, entry));
      li.addEventListener("keydown", (e) => {
        if (e.target === li && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          openEditor(li, entry);
        }
      });
    }
    return li;
  }

  function renderDraft(draft) {
    current = draft;
    const dayLabel = new Date(`${draft.target_day}T00:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    const visible = draft.entries.filter((e) => e.state !== "discarded");
    const pending = visible.filter((e) => e.state === "pending");
    const dropped = draft.rejected?.length ?? 0;

    show(
      [
        h("div", { class: "draft-head" }, icon("spark"), `Draft for ${dayLabel}`, h("span", { class: "chip small" }, draft.source === "llm" ? "model" : "rule-based")),
        visible.length
          ? h("ul", { class: "draft-list" }, visible.map((e, i) => entryRow(e, i)))
          : h("div", { class: "draft-note" }, "Nothing worth scheduling was proposed."),
        showReasons && dropped ? h("div", { class: "draft-note" }, `${dropped} proposed block${dropped === 1 ? " was" : "s were"} dropped by the checks (see Drafts).`) : null,
        h(
          "div",
          { class: "draft-actions" },
          pending.length ? button("filled", pending.length > 1 ? `Add all (${pending.length})` : "Add to schedule", () => accept(null)) : null,
          button("outlined", "Revise", () => renderRevise(draft)),
          button("text", "Discard draft", () => discard(null))
        ),
      ],
      "draft"
    );
  }

  function renderRevise(draft) {
    const input = h("input", { type: "text", maxlength: "500", "aria-label": "How should the draft change?", placeholder: "How should it change? e.g. keep the evening free" });
    const send = button("filled", "Prepare revision", null);
    const cancel = button("text", "Cancel", () => renderDraft(draft));

    send.onclick = async () => {
      const text = input.value.trim();
      if (!text) return input.focus();
      [input, send, cancel].forEach((c) => (c.disabled = true));
      const envelope = await call(reviseDraft(draft.id, text));
      if (envelope.status === "error") {
        fail(envelope);
        return [input, send, cancel].forEach((c) => (c.disabled = false));
      }
      renderApproval(envelope.data);
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") send.click();
      if (e.key === "Escape") cancel.click();
    });

    show(
      h("div", { class: "draft-form" }, input, h("div", { class: "draft-actions" }, send, cancel), h("div", { class: "draft-hint" }, "Blocks you added or edited stay exactly as they are.")),
      "revise"
    );
    input.focus();
  }

  return { el, refresh };
}
