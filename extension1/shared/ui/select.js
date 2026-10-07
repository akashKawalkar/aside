// shared/ui/select.js — Material outlined dropdown with an animated menu.

import { h } from "./dom.js";
import { icon } from "./icons.js";
import { openPopover } from "./popover.js";

/**
 * createSelect({ label, options: [{ value, label }], value, onChange })
 * Returns { el, getValue(), setValue(v), setOptions(options) }.
 */
export function createSelect({ label, options = [], value, onChange } = {}) {
  let items = options;
  let current = value ?? items[0]?.value;
  let popover = null;

  const text = h("span", { class: "select-text" });
  const button = h(
    "button",
    {
      type: "button",
      class: "select-button stateful",
      "aria-haspopup": "listbox",
      "aria-expanded": "false",
      onclick: toggle,
    },
    text,
    icon("arrow_drop_down")
  );
  const el = h("div", { class: "select-field" }, button, h("span", { class: "select-label" }, label));

  function labelFor(v) {
    return items.find((o) => o.value === v)?.label ?? "";
  }

  function paint() {
    text.textContent = labelFor(current);
  }

  function toggle() {
    if (popover) {
      popover.close();
      return;
    }

    const menu = h(
      "div",
      { class: "menu select-menu", role: "listbox" },
      items.map((option) =>
        h(
          "button",
          {
            type: "button",
            role: "option",
            class: "menu-item stateful",
            "aria-selected": String(option.value === current),
            onclick: () => {
              current = option.value;
              paint();
              popover?.close();
              onChange?.(current);
            },
          },
          option.label
        )
      )
    );

    button.setAttribute("aria-expanded", "true");
    popover = openPopover({
      anchor: button,
      content: menu,
      onClose: () => {
        popover = null;
        button.setAttribute("aria-expanded", "false");
      },
    });
    popover.el.style.minWidth = `${button.offsetWidth}px`;
    popover.reposition();
    menu.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    (menu.querySelector('[aria-selected="true"]') ?? menu.firstElementChild)?.focus();
  }

  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && !popover) {
      event.preventDefault();
      toggle();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (!popover) return;
    const entries = [...popover.el.querySelectorAll(".menu-item")];
    const index = entries.indexOf(document.activeElement);

    if (event.key === "ArrowDown") {
      event.preventDefault();
      entries[Math.min(entries.length - 1, index + 1)]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      entries[Math.max(0, index - 1)]?.focus();
    }
  });

  paint();

  return {
    el,
    getValue: () => current,
    setValue(v) {
      current = v;
      paint();
    },
    setOptions(next) {
      items = next;
      if (!items.some((o) => o.value === current)) current = items[0]?.value;
      paint();
    },
  };
}
