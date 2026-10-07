// settings/tabs/context-viewer.js — "why did it say that": every context compile, what was packed and dropped.
import { getCompiles, getCompile, replayCompile } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { createSelect } from "../../shared/ui/select.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const contextView = $("context-view");

const RECIPES = ["chat", "schedule", "extraction"];
const label = (name) => name.replaceAll("_", " ");

function line(source, text, tokens, extra) {
  return h(
    "div",
    { class: "ctx-line" },
    h("span", { class: "src" }, label(source)),
    h("span", { class: "txt" }, text),
    extra ?? null,
    tokens !== undefined ? h("span", { class: "tok" }, `${tokens} tok`) : null
  );
}

function breakdownTable(bySource) {
  const rows = Object.entries(bySource).map(([source, n]) =>
    h("tr", {}, h("td", {}, label(source)), h("td", {}, String(n.offered)), h("td", {}, String(n.chosen)),
      h("td", {}, String(n.dropped)), h("td", {}, String(n.tokens)))
  );
  return h(
    "div",
    { class: "data-table-wrap" },
    h(
      "table",
      { class: "data-table" },
      h("thead", {}, h("tr", {}, ["Source", "Offered", "Packed", "Dropped", "Tokens"].map((c) => h("th", {}, c)))),
      h("tbody", {}, rows)
    )
  );
}

function replaySection(compileId) {
  const result = h("div", {});
  const select = createSelect({
    label: "Replay under",
    options: RECIPES.map((name) => ({ value: name, label: name })),
    value: "extraction",
    onChange: run,
  });

  async function run(recipe = select.getValue()) {
    showLoading(result, 2);
    const envelope = await replayCompile(compileId, recipe);
    if (envelope.status === "error") {
      result.replaceChildren(emptyState("error", `Couldn't replay: ${envelopeError(envelope)}`));
      return;
    }
    const r = envelope.data;
    const lines = [
      ...r.only_in_original.map((id) => h("div", { class: "removed" }, `− ${id}`)),
      ...r.only_in_replay.map((id) => h("div", { class: "added" }, `+ ${id}`)),
    ];
    result.replaceChildren(
      h("p", { class: "supporting" }, `${r.tokens} of ${r.budget} tokens under "${r.recipe}", ${r.dropped.length} dropped.`),
      lines.length ? h("div", { class: "diff-lines" }, lines) : h("p", { class: "supporting" }, "Same items as the original."),
      h("pre", { class: "ctx-packed" }, r.text || "(nothing packed)")
    );
  }

  run();
  return h("div", {}, h("div", { class: "ctx-replay" }, select.el), result);
}

async function fillDetail(detail, id) {
  showLoading(detail, 3);
  const envelope = await getCompile(id);
  if (envelope.status === "error") {
    detail.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }
  const c = envelope.data;
  // A source that threw contributes nothing and shows up only as a dropped row; say so plainly, or the context just
  // looks thinner than it should.
  const failed = c.dropped.filter((d) => d.reason === "source_error").map((d) => d.source);

  detail.replaceChildren(
    ...(failed.length
      ? [h("p", { class: "supporting" }, h("span", { class: "chip small warn" }, "source failed"), ` ${failed.join(", ")} contributed nothing to this context. Check the server log.`)]
      : []),
    h("h4", { class: "review-sub" }, "By source"),
    breakdownTable(c.by_source),
    h("h4", { class: "review-sub" }, `Packed (${c.chosen.length})`),
    c.chosen.length
      ? h("div", { class: "ctx-lines" }, c.chosen.map((i) => line(i.source, i.text, i.tokens)))
      : h("p", { class: "supporting" }, "Nothing was packed."),
    h("h4", { class: "review-sub" }, `Dropped (${c.dropped.length})`),
    c.dropped.length
      ? h(
          "div",
          { class: "ctx-lines" },
          c.dropped.map((d) => line(d.source, d.id, d.tokens || undefined, h("span", { class: "chip small warn" }, d.reason)))
        )
      : h("p", { class: "supporting" }, "Nothing was dropped."),
    h("h4", { class: "review-sub" }, "Replay"),
    replaySection(id)
  );
}

function compileCard(c, i) {
  const detail = h("div", { class: "ctx-detail", hidden: true });
  let loaded = false;

  const card = h(
    "div",
    { class: "card log-card ctx-card", style: { "--i": String(Math.min(i, 12)) }, tabindex: "0" },
    h(
      "div",
      { class: "log-head" },
      h("span", { class: "chip small" }, c.recipe_name),
      h("span", {}, `#${c.id}`),
      h("span", {}, c.model),
      h("span", {}, `${c.tokens} tok · ${c.chosen_count} packed · ${c.dropped_count} dropped`),
      h("span", { class: "when" }, new Date(c.ts).toLocaleString())
    ),
    h("p", { class: "ctx-query" }, c.query || h("span", { class: "supporting" }, `(${c.situation}, no query)`)),
    detail
  );

  async function toggle() {
    detail.hidden = !detail.hidden;
    if (!detail.hidden && !loaded) {
      loaded = true;
      await fillDetail(detail, c.id);
    }
  }

  card.addEventListener("click", (e) => {
    if (!detail.contains(e.target)) toggle();   // clicks inside the opened detail (replay menu etc.) don't collapse it
  });
  card.addEventListener("keydown", (e) => {
    if (e.target === card && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      toggle();
    }
  });
  return card;
}

export async function loadContext() {
  showLoading(contextView, 3);
  const envelope = await getCompiles(50);

  if (envelope.status === "error") {
    contextView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const items = envelope.data?.items ?? [];
  if (items.length === 0) {
    contextView.replaceChildren(emptyState("spark", "No context compiled yet. It appears here once the agent builds a prompt."));
    return;
  }
  contextView.replaceChildren(...items.map(compileCard));
}
