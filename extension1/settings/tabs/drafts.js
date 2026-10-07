// settings/tabs/drafts.js — every schedule draft, newest first: why each block was proposed, what the checks dropped, and
// how the day turned out against the FIRST draft (plan 3.6: edits in between do not count).
import { compareDraftDay, getDrafts } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { formatTimeRange } from "../../shared/ui/datetime-utils.js";
import { $, emptyState, envelopeError, showLoading } from "./_shared.js";

const REASONS = {
  empty_title: "no title",
  title_too_long: "title too long",
  bad_times: "end was not after start",
  wrong_day: "not on the planned day",
  outside_waking_hours: "outside waking hours",
  too_short: "too short",
  too_long: "too long",
  too_many: "more blocks than a day can hold",
  unreadable_entry: "could not be read",
};

function plainReason(code) {
  if (code.startsWith("overlaps_fixed:")) return `overlapped ${code.slice("overlaps_fixed:".length)}`;
  if (code.startsWith("overlaps_draft:")) return `overlapped ${code.slice("overlaps_draft:".length)} in the same draft`;
  return REASONS[code] ?? code;
}

const when = (iso) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function stateChip(state) {
  const kind = { accepted: "ok", discarded: "", pending: "warn" }[state] ?? "";
  return h("span", { class: `chip small ${kind}`.trim() }, state);
}

// What the model proposed (never edited), with what became of it: the working copy at the same position.
function entryLine(proposed, working) {
  return h(
    "div",
    { class: "setting-row" },
    h(
      "div",
      { class: "setting-text" },
      h("div", { class: "title-medium" }, proposed.title),
      h("div", { class: "supporting" }, formatTimeRange(new Date(proposed.start_at), new Date(proposed.end_at))),
      proposed.reason ? h("div", { class: "supporting" }, proposed.reason) : null,
      working?.edited ? h("div", { class: "supporting" }, `You changed it to: ${working.title}, ${formatTimeRange(new Date(working.start_at), new Date(working.end_at))}`) : null
    ),
    stateChip(working?.state ?? "pending")
  );
}

function rejectedBlock(rejected) {
  if (!rejected.length) return null;
  return h(
    "details",
    { class: "setting-row" },
    h("summary", { class: "supporting" }, `${rejected.length} proposed block${rejected.length === 1 ? "" : "s"} dropped by the checks`),
    h(
      "ul",
      { class: "review-list" },
      ...rejected.map((r) => h("li", {}, `${r.entry.title ?? "(unreadable)"}: ${plainReason(r.reason)}`))
    )
  );
}

function versionCard(draft) {
  const head = [
    h("span", { class: "title-medium" }, `Version ${draft.version}`),
    h("span", { class: "chip small" }, draft.source === "llm" ? "model" : "rule-based"),
    h("span", { class: `chip small ${draft.status === "accepted" ? "ok" : draft.status === "open" ? "warn" : ""}`.trim() }, draft.status),
  ];
  return h(
    "div",
    { class: "card setting-card" },
    h("div", { class: "setting-row" }, h("div", { class: "setting-text" }, h("div", { class: "draft-actions" }, ...head), h("div", { class: "supporting" }, `${when(draft.created_at)}${draft.model ? ` · ${draft.model}` : ""}`))),
    draft.instruction ? h("div", { class: "setting-row" }, h("div", { class: "supporting" }, `Instruction: ${draft.instruction}`)) : null,
    ...(draft.proposed.length ? draft.proposed.map((p, i) => entryLine(p, draft.entries[i])) : [h("div", { class: "setting-row" }, h("div", { class: "supporting" }, "Nothing was proposed."))]),
    rejectedBlock(draft.rejected ?? [])
  );
}

function comparisonBlock(day, firstVersion) {
  const body = h("div", { class: "supporting" }, "Loading…");
  const box = h("div", { class: "card setting-card" }, h("div", { class: "setting-row" }, h("div", { class: "setting-text" }, h("div", { class: "title-medium" }, `How ${day} went against version ${firstVersion}`), body)));

  compareDraftDay(day).then((envelope) => {
    if (envelope.status === "error") return body.replaceChildren(envelopeError(envelope));
    const { comparison: c, final_source: source } = envelope.data;
    const lines = [
      `${c.kept.length} kept as proposed, ${c.moved.length} moved, ${c.dropped.length} dropped, ${c.added.length} added by you.`,
      source === "live" ? "No end-of-day snapshot exists for this day, so this compares against the schedule as it stands." : "Compared against the end-of-day snapshot.",
      ...c.moved.map((m) => `Moved: ${m.title}`),
      ...c.dropped.map((m) => `Dropped: ${m.title}`),
      ...c.added.map((m) => `Added: ${m.title}`),
    ];
    body.replaceChildren(...lines.map((line) => h("div", {}, line)));
  });
  return box;
}

export async function loadDrafts() {
  const list = $("drafts-list");
  showLoading(list, 3);
  const envelope = await getDrafts(60);

  if (envelope.status === "error") {
    list.replaceChildren(emptyState("sync_problem", envelopeError(envelope)));
    return;
  }

  const drafts = envelope.data.drafts;
  if (!drafts.length) {
    list.replaceChildren(emptyState("spark", "No drafts yet. \"Draft tomorrow\" appears in the side panel when tomorrow is empty."));
    return;
  }

  const byDay = new Map();
  for (const draft of drafts) byDay.set(draft.target_day, [...(byDay.get(draft.target_day) ?? []), draft]);

  const nodes = [];
  for (const [day, versions] of byDay) {
    const label = new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    const first = Math.min(...versions.map((v) => v.version));
    nodes.push(h("h3", { class: "group-title" }, label), ...versions.map(versionCard), comparisonBlock(day, first));
  }
  list.replaceChildren(...nodes);
}
