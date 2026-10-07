// shared/ui/datepicker.js — Google Calendar style month grid (Monday first).

import { h } from "./dom.js";
import { icon } from "./icons.js";
import {
  MONTH_LABELS,
  WEEKDAY_HEADERS,
  addDays,
  formatDate,
  monthGrid,
  sameDay,
  startOfDay,
} from "./datetime-utils.js";

/**
 * createDatePicker({ value, onSelect, now })
 *  - value: Date (the selected day; time part ignored)
 *  - onSelect(date): called with a local-midnight Date when a day is chosen
 * Returns { el, setValue(date), focus() }.
 */
export function createDatePicker({ value = new Date(), onSelect, now = new Date() } = {}) {
  let selected = startOfDay(value);
  let focused = selected;
  let viewYear = selected.getFullYear();
  let viewMonth = selected.getMonth();

  const label = h("button", { class: "dp-label stateful", type: "button", "aria-live": "polite" });
  const prev = h(
    "button",
    { class: "icon-btn small stateful", type: "button", "aria-label": "Previous month", onclick: () => shiftMonth(-1) },
    icon("chevron_left")
  );
  const next = h(
    "button",
    { class: "icon-btn small stateful", type: "button", "aria-label": "Next month", onclick: () => shiftMonth(1) },
    icon("chevron_right")
  );
  const grid = h("div", { class: "dp-grid", role: "grid" });

  const el = h(
    "div",
    { class: "dp" },
    h("div", { class: "dp-header" }, label, h("span", { class: "dp-spacer" }), prev, next),
    h(
      "div",
      { class: "dp-weekdays", "aria-hidden": "true" },
      WEEKDAY_HEADERS.map((d) => h("span", {}, d))
    ),
    grid
  );

  label.addEventListener("click", () => goTo(startOfDay(now)));

  function render(direction = 0) {
    label.textContent = `${MONTH_LABELS[viewMonth]} ${viewYear}`;
    grid.replaceChildren();

    for (const day of monthGrid(viewYear, viewMonth)) {
      const isSelected = sameDay(day, selected);
      const button = h(
        "button",
        {
          type: "button",
          role: "gridcell",
          class: [
            "dp-day",
            "stateful",
            day.getMonth() !== viewMonth ? "outside" : "",
            sameDay(day, now) ? "today" : "",
            isSelected ? "selected" : "",
          ].filter(Boolean).join(" "),
          tabindex: sameDay(day, focused) ? "0" : "-1",
          "aria-selected": String(isSelected),
          "aria-label": formatDate(day, now),
          dataset: { time: String(day.getTime()) },
          onclick: () => choose(day),
        },
        String(day.getDate())
      );
      grid.appendChild(button);
    }

    if (direction !== 0) {
      grid.classList.remove("slide-next", "slide-prev");
      void grid.offsetWidth; // restart the animation
      grid.classList.add(direction > 0 ? "slide-next" : "slide-prev");
    }
  }

  function shiftMonth(delta) {
    const target = new Date(viewYear, viewMonth + delta, 1);
    viewYear = target.getFullYear();
    viewMonth = target.getMonth();
    const day = Math.min(focused.getDate(), new Date(viewYear, viewMonth + 1, 0).getDate());
    focused = new Date(viewYear, viewMonth, day);
    render(delta);
  }

  function goTo(day) {
    const direction = day > focused ? 1 : -1;
    const changedMonth = day.getFullYear() !== viewYear || day.getMonth() !== viewMonth;
    focused = day;
    viewYear = day.getFullYear();
    viewMonth = day.getMonth();
    render(changedMonth ? direction : 0);
  }

  function choose(day) {
    selected = startOfDay(day);
    focused = selected;
    render();
    onSelect?.(selected);
  }

  function focusDay() {
    grid.querySelector('.dp-day[tabindex="0"]')?.focus();
  }

  grid.addEventListener("keydown", (event) => {
    const moves = {
      ArrowLeft: () => addDays(focused, -1),
      ArrowRight: () => addDays(focused, 1),
      ArrowUp: () => addDays(focused, -7),
      ArrowDown: () => addDays(focused, 7),
      PageUp: () => new Date(focused.getFullYear(), focused.getMonth() - 1, focused.getDate()),
      PageDown: () => new Date(focused.getFullYear(), focused.getMonth() + 1, focused.getDate()),
      Home: () => addDays(focused, -((focused.getDay() + 6) % 7)),
      End: () => addDays(focused, 6 - ((focused.getDay() + 6) % 7)),
    };

    if (moves[event.key]) {
      event.preventDefault();
      goTo(moves[event.key]());
      focusDay();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(focused);
      focusDay();
    }
  });

  render();

  return {
    el,
    focus: focusDay,
    setValue(date) {
      selected = startOfDay(date);
      focused = selected;
      viewYear = selected.getFullYear();
      viewMonth = selected.getMonth();
      render();
    },
  };
}
