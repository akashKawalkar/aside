// shared/ui/reply-notes.js — plain-language notes about HOW a reply came back, so a cut-off or substituted answer is never
// mistaken for a normal one.
import { h } from "./dom.js";
import { icon } from "./icons.js";

export function replyNotes(data = {}) {
  const notes = [];
  if (data.finish_reason === "length") notes.push("The reply was cut off at the length limit, so it may be incomplete.");
  if (data.fell_back_from) notes.push(`${data.fell_back_from} was busy, so ${data.model} answered instead.`);
  return notes;
}

/** One note per line, with an icon: put them under a reply. */
export function noteLines(data) {
  return replyNotes(data).map((text) => h("div", { class: "note-line" }, icon("error"), text));
}
