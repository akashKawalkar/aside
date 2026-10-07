// settings/tabs/notes.js
import { getNotes, updateNote, deleteNote } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { icon } from "../../shared/ui/icons.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { $, envelopeError, emptyState, showLoading, removeRow } from "./_shared.js";

const view = $("notes-view");
const filterView = $("notes-tag-filters");
let activeTag = null;
let allNotes = [];

function parseDate(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

function noteRow(note, reload) {
  const textInput = h("input", { class: "gt-title", type: "text", value: note.text, maxlength: "10000", "aria-label": "Note text" });
  
  const chips = [];
  if (note.source) chips.push(h("span", { class: "chip small" }, note.source));
  
  // Date chip
  const dateStr = parseDate(note.created_at);
  if (dateStr) {
    chips.push(h("span", { class: "chip small muted" }, dateStr));
  }
  
  // Tag chips
  const tags = note.tags || [];
  tags.forEach(t => {
    chips.push(h("span", { class: "chip small" }, t));
  });

  const delBtn = h("button", { type: "button", class: "icon-btn small stateful gt-delete", "aria-label": "Delete note", title: "Delete note" }, icon("delete"));
  const row = h("li", { class: "gt-row note-row" }, h("div", { class: "gt-main" }, textInput, chips.length ? h("div", { class: "pf-chips" }, chips) : null), delBtn);

  let saved = note.text;
  
  async function commit() {
    const next = textInput.value.trim();
    if (next === saved) return;
    if (!next) { showSnackbar("Note cannot be empty."); textInput.value = saved; return; }
    textInput.disabled = true;
    const envelope = await updateNote(note.id, { text: next, tags: note.tags });
    textInput.disabled = false;
    if (envelope.status === "error") { showSnackbar(envelopeError(envelope)); textInput.value = saved; return; }
    saved = envelope.data.note.text;
    textInput.value = saved;
    row.classList.remove("saved");
    void row.offsetWidth;
    row.classList.add("saved");
  }

  textInput.addEventListener("blur", commit);
  textInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); textInput.blur(); }
    else if (e.key === "Escape") { textInput.value = saved; textInput.blur(); }
  });

  delBtn.addEventListener("click", async () => {
    delBtn.disabled = true;
    const envelope = await deleteNote(note.id);
    if (envelope.status === "error") { delBtn.disabled = false; showSnackbar(envelopeError(envelope)); return; }
    await removeRow(row, reload);
    showSnackbar("Note deleted.");
  });
  
  return row;
}

function renderFilters() {
  const allTags = new Set();
  allNotes.forEach(n => (n.tags || []).forEach(t => allTags.add(t)));
  const tags = Array.from(allTags).sort();
  
  const chips = [
    h("button", { 
      class: `chip stateful ${activeTag === null ? "filled" : ""}`,
      onclick: () => { activeTag = null; renderNotes(); } 
    }, "All")
  ];
  
  tags.forEach(tag => {
    chips.push(h("button", { 
      class: `chip stateful ${activeTag === tag ? "filled" : ""}`,
      onclick: () => { activeTag = tag; renderNotes(); } 
    }, tag));
  });
  
  filterView.replaceChildren(...chips);
}

function renderNotes() {
  renderFilters();
  const visible = activeTag === null ? allNotes : allNotes.filter(n => (n.tags || []).includes(activeTag));
  
  if (!visible.length) {
    view.replaceChildren(h("p", { class: "supporting pf-none", style: "padding: 16px;" }, "No notes found."));
    return;
  }
  
  const reload = () => loadNotes({ quiet: true });
  view.replaceChildren(h("ul", { class: "gt-list" }, visible.map(n => noteRow(n, reload))));
}

export async function loadNotes({ quiet = false } = {}) {
  if (!quiet) showLoading(view, 4);
  const envelope = await getNotes(200);
  if (envelope.status === "error") {
    view.replaceChildren(emptyState("error", `Couldn't load notes: ${envelopeError(envelope)}`));
    return;
  }
  allNotes = envelope.data.notes || [];
  renderNotes();
}

