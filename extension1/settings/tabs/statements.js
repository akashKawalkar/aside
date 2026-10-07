// settings/tabs/statements.js — what the user said (statements), the facts extracted from it (candidate items) and the
// one-shot instructions behind schedule drafts. Read-only here.
import { api } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { $, emptyState, envelopeError, showLoading } from "./_shared.js";

const day = (value) => (value ? String(value).slice(0, 10) : "");
const span = (a, b) => (a || b ? `${day(a) || "…"} → ${day(b) || "…"}` : "");

// How each kind of record reads: a headline and a quiet second line.
const KINDS = {
  statements: (r) => ({ title: r.text, detail: [r.source, day(r.created_at)].filter(Boolean).join(" · ") }),
  candidate_items: (r) => ({
    title: r.text,
    detail: [r.item_type, r.effect && `→ ${r.effect}`, span(r.valid_from, r.valid_until), `confidence ${Math.round((r.confidence ?? 1) * 100)}%`, r.reason]
      .filter(Boolean)
      .join(" · "),
  }),
  instruction_records: (r) => ({ title: r.text, detail: [span(r.valid_from, r.valid_until), day(r.created_at)].filter(Boolean).join(" · ") }),
};

export async function loadStatements() {
  await Promise.all([
    fetchAndRender("/statements", "statements-list", "statements"),
    fetchAndRender("/candidate_items", "candidate-items-list", "candidate_items"),
    fetchAndRender("/instruction_records", "instruction-records-list", "instruction_records"),
  ]);
}

async function fetchAndRender(endpoint, containerId, kind) {
  const container = $(containerId);
  if (!container) return;
  showLoading(container, 1);

  const envelope = await api.get(endpoint);
  if (envelope.status !== "ok") {
    container.replaceChildren(emptyState("sync_problem", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const items = envelope.data.items ?? [];
  if (!items.length) {
    container.replaceChildren(h("div", { class: "supporting" }, "Nothing here yet."));
    return;
  }

  container.replaceChildren(
    ...items.map((item) => {
      const { title, detail } = KINDS[kind](item);
      return h(
        "div",
        { class: "card setting-card" },
        h("div", { class: "setting-row" }, h("div", { class: "setting-text" }, h("div", { class: "title-medium" }, title), detail ? h("div", { class: "supporting" }, detail) : null))
      );
    })
  );
}
