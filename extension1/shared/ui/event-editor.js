// shared/ui/event-editor.js — inline editor for a schedule entry.
// Used by the side panel tile and the settings schedule page.

import { h } from "./dom.js";
import { createRangeFields } from "./datetime-field.js";

/**
 * createEventEditor({ entry, onSave, onDelete, onCancel })
 *  - entry: { title, start_at, end_at }
 *  - onSave({ title, start, end }) and onDelete() return Promise<boolean>:
 *    true when it worked (the caller then re-renders), false to stay open.
 *  - onCancel(): called for Cancel and Escape.
 * Returns { el, focus() }.
 */
export function createEventEditor({ entry, onSave, onDelete, onCancel } = {}) {
  const titleInput = h("input", {
    type: "text",
    placeholder: " ",
    maxlength: "200",
    value: entry.title,
    "aria-label": "Title",
  });
  const titleField = h("label", { class: "text-field" }, titleInput, h("span", { class: "label" }, "Title"));
  titleInput.addEventListener("input", () => titleField.classList.remove("error"));

  const range = createRangeFields({
    start: new Date(entry.start_at),
    end: new Date(entry.end_at),
  });

  const save = h("button", { type: "button", class: "btn filled compact stateful" }, "Save");
  const del = h("button", { type: "button", class: "btn text danger compact stateful" }, "Delete");
  const cancel = h("button", { type: "button", class: "btn text compact stateful" }, "Cancel");

  const controls = [titleInput, save, del, cancel];
  const setBusy = (busy) => controls.forEach((c) => (c.disabled = busy));

  save.addEventListener("click", async () => {
    const title = titleInput.value.trim();
    if (!title) {
      titleField.classList.add("error");
      titleInput.focus();
      return;
    }

    const { start, end } = range.getRange();
    setBusy(true);
    const done = await onSave?.({ title, start, end });
    if (!done) setBusy(false); // keep the editor open so the edit isn't lost
  });

  del.addEventListener("click", async () => {
    setBusy(true);
    const done = await onDelete?.();
    if (!done) setBusy(false);
  });

  cancel.addEventListener("click", () => onCancel?.());

  const el = h(
    "div",
    {
      class: "event-editor",
      // Clicks inside the editor must not bubble to a row that would reopen it.
      onclick: (e) => e.stopPropagation(),
      onkeydown: (e) => {
        if (e.key === "Escape") onCancel?.();
      },
    },
    titleField,
    range.el,
    h("div", { class: "actions" }, del, h("span", { class: "spacer" }), cancel, save)
  );

  return { el, focus: () => titleInput.focus() };
}
