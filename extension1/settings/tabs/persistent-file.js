// settings/tabs/persistent-file.js — the persistent-file editor: five sections of entries, edited in place.
import {
  getPersistentFile, addPersistentEntry, editPersistentEntry, confirmPersistentEntry, retirePersistentEntry,
} from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { icon } from "../../shared/ui/icons.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { createSelect } from "../../shared/ui/select.js";
import { $, envelopeError, emptyState, showLoading, removeRow } from "./_shared.js";

const view = $("persistent-view");
const NO_REPLACE = "";

const clip = (text, n = 48) => (text.length > n ? `${text.slice(0, n - 1)}…` : text);

function entryRow(entry, settleDays) {
  let saved = entry.text;
  const title = h("input", { class: "gt-title", type: "text", value: saved, maxlength: "500", "aria-label": "Entry" });

  const chips = [];
  if (entry.status === "provisional") {
    chips.push(h("span", { class: "chip small warn", title: "Counts as settled after being seen on separate days" },
      `provisional · ${entry.seen_days.length}/${settleDays} days`));
  }
  if (entry.evidence_count > 1) chips.push(h("span", { class: "chip small" }, `seen ×${entry.evidence_count}`));
  if (entry.source !== "user") chips.push(h("span", { class: "chip small" }, entry.source));

  const confirm = h("button", { type: "button", class: "icon-btn small stateful gt-delete", "aria-label": "Still true", title: "Still true" }, icon("check"));
  const retire = h("button", { type: "button", class: "icon-btn small stateful gt-delete", "aria-label": "Retire entry", title: "Retire: out of every prompt, kept in the log" }, icon("delete"));
  const row = h("li", { class: "gt-row" }, h("div", { class: "gt-main" }, title, chips.length ? h("div", { class: "pf-chips" }, chips) : null), confirm, retire);

  async function commit() {
    const next = title.value.trim();
    if (next === saved) return;
    if (!next) { showSnackbar("An entry needs text."); title.value = saved; return; }
    title.disabled = true;
    const envelope = await editPersistentEntry(entry.id, { text: next });
    title.disabled = false;
    if (envelope.status === "error") { showSnackbar(envelopeError(envelope)); title.value = saved; return; }
    saved = envelope.data.entry.text;
    title.value = saved;
    row.classList.remove("saved");
    void row.offsetWidth;
    row.classList.add("saved");
    if (envelope.data.evicted.length) showSnackbar(`Over the size cap: ${envelope.data.evicted.length} older entr${envelope.data.evicted.length === 1 ? "y was" : "ies were"} retired.`);
  }
  title.addEventListener("blur", commit);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); title.blur(); }
    else if (e.key === "Escape") { title.value = saved; title.blur(); }
  });

  confirm.addEventListener("click", async () => {
    const envelope = await confirmPersistentEntry(entry.id);
    if (envelope.status === "error") { showSnackbar(envelopeError(envelope)); return; }
    showSnackbar(envelope.data.also_changed.length ? "Settled: the entry it replaced is retired." : "Counted as seen again.");
    await loadPersistentFile({ quiet: true });
  });

  retire.addEventListener("click", async () => {
    retire.disabled = true;
    const envelope = await retirePersistentEntry(entry.id);
    if (envelope.status === "error") { retire.disabled = false; showSnackbar(envelopeError(envelope)); return; }
    await removeRow(row, () => loadPersistentFile({ quiet: true }));
    showSnackbar("Retired. Undo it under What changed.");
  });
  return row;
}

/** The add box for one section. A new fact may name the live entry it replaces; it then starts provisional. */
function addBox(name, live) {
  const input = h("input", { class: "pf-input", type: "text", maxlength: "500", placeholder: "Add an entry", "aria-label": `Add to ${name}` });
  const replaces = createSelect({
    label: "Replaces",
    options: [{ value: NO_REPLACE, label: "Nothing" }, ...live.map((e) => ({ value: String(e.id), label: clip(e.text) }))],
    value: NO_REPLACE,
  });
  const add = h("button", { type: "button", class: "btn tonal compact stateful" }, "Add");

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    add.disabled = true;
    const body = { section: name, text };
    if (replaces.getValue() !== NO_REPLACE) body.replaces_id = Number(replaces.getValue());
    const envelope = await addPersistentEntry(body);
    add.disabled = false;
    if (envelope.status === "error") { showSnackbar(envelopeError(envelope)); return; }
    showSnackbar(envelope.message);
    if (envelope.data.evicted.length) showSnackbar(`Over the size cap: ${envelope.data.evicted.length} older entr${envelope.data.evicted.length === 1 ? "y was" : "ies were"} retired.`);
    await loadPersistentFile({ quiet: true });
  }
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
  return h("div", { class: "pf-add" }, input, live.length ? replaces.el : null, add);
}

function sectionBlock(name, section, settleDays) {
  const live = section.entries.filter((e) => e.status !== "retired");
  return h(
    "div",
    { class: "pf-section" },
    h("h3", { class: "group-title" }, h("span", { class: "pf-name" }, name), h("span", { class: "chip small" }, section.always_on ? "always in the prompt" : "chosen per situation")),
    h("p", { class: "supporting" }, section.about),
    h("div", { class: "card" },
      live.length ? h("ul", { class: "gt-list" }, live.map((e) => entryRow(e, settleDays))) : h("p", { class: "supporting pf-none" }, "Nothing here yet."),
      addBox(name, live)
    )
  );
}

function retiredBlock(sections) {
  const rows = Object.entries(sections).flatMap(([name, s]) => s.entries.filter((e) => e.status === "retired").map((e) => ({ name, ...e })));
  if (!rows.length) return null;
  const reasons = { replaced: "replaced", evicted: "over the size cap", user: "retired by you" };
  return h(
    "details",
    { class: "pf-retired" },
    h("summary", {}, `Retired (${rows.length})`),
    h("p", { class: "supporting" }, "Never sent to the model. Undo a change under What changed to bring one back."),
    h("ul", { class: "hist-list" }, rows.map((e) => h("li", { class: "pf-retired-row" }, h("span", {}, e.text), h("span", { class: "chip small" }, e.name), h("span", { class: "chip small" }, reasons[e.retired_reason] ?? "retired"))))
  );
}

export async function loadPersistentFile({ quiet = false } = {}) {
  if (!quiet) showLoading(view, 4);
  const envelope = await getPersistentFile();
  if (envelope.status === "error") {
    view.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }
  const { sections, tokens, cap_tokens: cap, settle_days: settleDays } = envelope.data;
  view.replaceChildren(
    h("p", { class: `supporting pf-size${tokens > cap ? " over" : ""}` }, `About ${tokens} of ${cap} tokens used. Past the cap, the entries seen least are retired.`),
    ...Object.entries(sections).map(([name, section]) => sectionBlock(name, section, settleDays)),
    ...[retiredBlock(sections)].filter(Boolean)
  );
}
