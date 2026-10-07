// shared/ui/timepicker.js — Google Calendar style time list (15-minute steps).

import { h } from "./dom.js";
import { formatDuration, formatTime, timeSlots, withTime } from "./datetime-utils.js";

const STEP_MINUTES = 15;

/**
 * createTimeList({ value, rangeStart, onSelect, step })
 *  - value: Date (selected time; only the time of day matters unless rangeStart is given)
 *  - rangeStart: when set, this is an END-time list: it starts just after
 *    rangeStart, runs for 24 hours, and shows "(1 hr)" style durations
 *  - onSelect(date): called with a full Date
 * Returns { el, scrollToSelected(), focusSelected() }.
 */
export function createTimeList({ value, rangeStart = null, onSelect, step = STEP_MINUTES } = {}) {
  const options = buildOptions({ value, rangeStart, step });
  const el = h("div", { class: "tl", role: "listbox", tabindex: "-1" });

  let selectedIndex = options.findIndex((o) => o.date.getTime() === (value?.getTime() ?? NaN));

  options.forEach((option, index) => {
    const item = h(
      "button",
      {
        type: "button",
        role: "option",
        class: "tl-item stateful",
        tabindex: "-1",
        "aria-selected": String(index === selectedIndex),
        dataset: { index: String(index) },
        onclick: () => onSelect?.(option.date),
      },
      h("span", { class: "tl-time" }, formatTime(option.date)),
      option.hint ? h("span", { class: "tl-hint" }, `(${option.hint})`) : null
    );
    el.appendChild(item);
  });

  const items = () => [...el.querySelectorAll(".tl-item")];

  /** Index of the option closest to the current value (the selected one if exact). */
  function anchorIndex() {
    if (selectedIndex >= 0) return selectedIndex;
    if (!value) return 0;

    const target = rangeStart ? value.getTime() : withTime(options[0].date, {
      hours: value.getHours(),
      minutes: value.getMinutes(),
    }).getTime();

    let best = 0;
    let bestDelta = Infinity;
    options.forEach((o, i) => {
      const delta = Math.abs(o.date.getTime() - target);
      if (delta < bestDelta) {
        best = i;
        bestDelta = delta;
      }
    });
    return best;
  }

  function scrollToSelected() {
    const target = items()[anchorIndex()];
    if (!target) return;
    el.scrollTop = Math.max(0, target.offsetTop - el.clientHeight / 2 + target.offsetHeight / 2);
  }

  function focusSelected() {
    const target = items()[anchorIndex()];
    target?.setAttribute("tabindex", "0");
    target?.focus({ preventScroll: true });
  }

  el.addEventListener("keydown", (event) => {
    const list = items();
    const current = list.indexOf(document.activeElement);
    let to = null;

    if (event.key === "ArrowDown") to = Math.min(list.length - 1, current + 1);
    else if (event.key === "ArrowUp") to = Math.max(0, current - 1);
    else if (event.key === "Home") to = 0;
    else if (event.key === "End") to = list.length - 1;
    else return;

    event.preventDefault();
    list[current]?.setAttribute("tabindex", "-1");
    list[to].setAttribute("tabindex", "0");
    list[to].focus();
  });

  return { el, scrollToSelected, focusSelected };
}

function buildOptions({ value, rangeStart, step }) {
  if (rangeStart) {
    const count = Math.floor((24 * 60) / step);
    return Array.from({ length: count }, (_, k) => {
      const date = new Date(rangeStart.getTime() + (k + 1) * step * 60_000);
      return { date, hint: formatDuration(date.getTime() - rangeStart.getTime()) };
    });
  }

  const day = value ? new Date(value.getFullYear(), value.getMonth(), value.getDate()) : new Date();
  return timeSlots(step).map((minutes) => ({
    date: withTime(day, { hours: Math.floor(minutes / 60), minutes: minutes % 60 }),
    hint: null,
  }));
}
