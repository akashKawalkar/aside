// shared/ui/datetime-field.js — Google Calendar style date/time inputs.
//
//   createDateInput / createTimeInput   typed fields that open a picker popover
//   createDateTimeField                 a date + time pair for one moment
//   createRangeFields                   start/end pair; moving the start keeps the duration
//   openDuePicker                       calendar + time popover for a task due date

import { h } from "./dom.js";
import { icon } from "./icons.js";
import { openPopover } from "./popover.js";
import { createDatePicker } from "./datepicker.js";
import { createTimeList } from "./timepicker.js";
import {
  formatDate,
  addDays,
  formatTime,
  parseDate,
  parseTime,
  startOfDay,
  withTime,
} from "./datetime-utils.js";

// ---------- date input ----------

export function createDateInput({ value, onChange, now = new Date() } = {}) {
  let current = startOfDay(value);
  let popover = null;
  let suppressOpen = false;

  const input = h("input", {
    class: "dt-text",
    type: "text",
    "aria-label": "Date",
    autocomplete: "off",
    spellcheck: "false",
    value: formatDate(current, now),
  });
  const el = h("div", { class: "dt-input dt-date" }, input);

  function setDate(date, emit) {
    current = startOfDay(date);
    input.value = formatDate(current, now);
    el.classList.remove("error");
    if (emit) onChange?.(current);
  }

  function refocus() {
    suppressOpen = true;
    input.focus();
    suppressOpen = false;
  }

  function openPicker() {
    if (popover) return;

    const picker = createDatePicker({
      value: current,
      now,
      onSelect: (day) => {
        setDate(day, true);
        popover?.close();
        refocus();
      },
    });

    popover = openPopover({
      anchor: el,
      content: picker.el,
      className: "dp-popover",
      onClose: () => {
        popover = null;
      },
    });
  }

  function commit() {
    if (input.value === formatDate(current, now)) return;

    const parsed = parseDate(input.value, now);
    if (parsed) {
      setDate(parsed, true);
    } else {
      flagError(el);
      input.value = formatDate(current, now);
    }
  }

  input.addEventListener("focus", () => {
    input.select();
    if (!suppressOpen) openPicker();
  });
  input.addEventListener("click", openPicker);
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
      popover?.close();
    } else if (event.key === "ArrowDown") {
      openPicker();
    } else if (event.key === "Escape") {
      input.value = formatDate(current, now);
    }
  });

  return {
    el,
    input,
    getValue: () => current,
    setValue: (date) => setDate(date, false),
    setError: (on) => el.classList.toggle("error", on),
  };
}

// ---------- time input ----------

/**
 * `getRangeStart` (optional) makes this an end-time field: the list starts
 * after that moment and shows durations.
 */
export function createTimeInput({ value, onChange, getRangeStart, now = new Date() } = {}) {
  let current = new Date(value);
  let popover = null;
  let suppressOpen = false;

  const input = h("input", {
    class: "dt-text",
    type: "text",
    "aria-label": "Time",
    autocomplete: "off",
    spellcheck: "false",
    value: formatTime(current),
  });
  const el = h("div", { class: "dt-input dt-time" }, input);

  function setTime(date, emit) {
    current = new Date(date);
    input.value = formatTime(current);
    el.classList.remove("error");
    if (emit) onChange?.(current);
  }

  function refocus() {
    suppressOpen = true;
    input.focus();
    suppressOpen = false;
  }

  function openList() {
    if (popover) return;

    const list = createTimeList({
      value: current,
      rangeStart: getRangeStart?.() ?? null,
      onSelect: (date) => {
        setTime(date, true);
        popover?.close();
        refocus();
      },
    });

    popover = openPopover({
      anchor: el,
      content: list.el,
      className: "tl-popover",
      onClose: () => {
        popover = null;
      },
    });
    list.scrollToSelected();
    popover.reposition();
  }

  function commit() {
    if (input.value === formatTime(current)) return;

    const parsed = parseTime(input.value, { assumePm: current.getHours() >= 12 });
    if (parsed) {
      setTime(withTime(current, parsed), true);
    } else {
      flagError(el);
      input.value = formatTime(current);
    }
  }

  input.addEventListener("focus", () => {
    input.select();
    if (!suppressOpen) openList();
  });
  input.addEventListener("click", openList);
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
      popover?.close();
    } else if (event.key === "ArrowDown") {
      openList();
    } else if (event.key === "Escape") {
      input.value = formatTime(current);
    }
  });

  return {
    el,
    input,
    getValue: () => current,
    setValue: (date) => setTime(date, false),
    setError: (on) => el.classList.toggle("error", on),
  };
}

function flagError(el) {
  el.classList.remove("shake");
  void el.offsetWidth;
  el.classList.add("shake");
  setTimeout(() => el.classList.remove("shake"), 450);
}

// ---------- one moment: date + time ----------

export function createDateTimeField({ value, onChange, getRangeStart, now = new Date() } = {}) {
  let current = new Date(value);

  const date = createDateInput({
    value: current,
    now,
    onChange: (day) => {
      current = withTime(day, { hours: current.getHours(), minutes: current.getMinutes() });
      time.setValue(current);
      onChange?.(new Date(current));
    },
  });

  const time = createTimeInput({
    value: current,
    now,
    getRangeStart,
    onChange: (picked) => {
      current = picked;
      date.setValue(current);
      onChange?.(new Date(current));
    },
  });

  const el = h("div", { class: "dt-field" }, date.el, time.el);

  return {
    el,
    getValue: () => new Date(current),
    setValue(next) {
      current = new Date(next);
      date.setValue(current);
      time.setValue(current);
    },
    setError(on) {
      date.setError(on);
      time.setError(on);
    },
  };
}

// ---------- start / end ----------

/**
 * Two linked fields. Changing the start moves the end by the same amount so
 * the duration is kept; an end that is not after the start is refused.
 * onChange({ start, end }) fires only for valid ranges.
 */
export function createRangeFields({ start, end, onChange, now = new Date() } = {}) {
  const range = { start: new Date(start), end: new Date(end) };
  const message = h("div", { class: "range-error", role: "alert" });

  const snapshot = () => ({ start: new Date(range.start), end: new Date(range.end) });

  const startField = createDateTimeField({
    value: range.start,
    now,
    onChange: (picked) => {
      const duration = range.end.getTime() - range.start.getTime();
      range.start = picked;
      range.end = new Date(picked.getTime() + duration);
      endField.setValue(range.end);
      showError("");
      onChange?.(snapshot());
    },
  });

  const endField = createDateTimeField({
    value: range.end,
    now,
    getRangeStart: () => range.start,
    onChange: (picked) => {
      if (picked.getTime() <= range.start.getTime()) {
        showError("End must be after the start.");
        endField.setValue(range.end); // revert
        endField.setError(true);
        return;
      }
      range.end = picked;
      showError("");
      onChange?.(snapshot());
    },
  });

  function showError(text) {
    message.textContent = text;
    message.classList.toggle("visible", Boolean(text));
    if (!text) endField.setError(false);
  }

  const el = h(
    "div",
    { class: "range-fields" },
    h("div", { class: "range-row" }, h("span", { class: "range-label" }, "Start"), startField.el),
    h("div", { class: "range-row" }, h("span", { class: "range-label" }, "End"), endField.el),
    message
  );

  return {
    el,
    getRange: snapshot,
    setRange({ start: s, end: e }) {
      range.start = new Date(s);
      range.end = new Date(e);
      startField.setValue(range.start);
      endField.setValue(range.end);
      showError("");
    },
  };
}

// ---------- due-date popover (tasks) ----------

/**
 * openDuePicker({ anchor, value, onSave, now })
 * A calendar plus a time row, with Cancel / Save. Calls onSave(Date).
 */
export function openDuePicker({ anchor, value, onSave, now = new Date() } = {}) {
  let chosen = new Date(value);
  let popover = null;

  const picker = createDatePicker({
    value: chosen,
    now,
    onSelect: (day) => {
      chosen = withTime(day, { hours: chosen.getHours(), minutes: chosen.getMinutes() });
      timeLabel.textContent = formatTime(chosen);
    },
  });

  const timeLabel = h("span", { class: "due-time-value" }, formatTime(chosen));
  const calendarPane = h("div", { class: "due-collapse" }, h("div", {}, picker.el));
  const listInner = h("div", {});
  const listPane = h("div", { class: "due-collapse closed" }, listInner);

  const timeButton = h(
    "button",
    {
      type: "button",
      class: "due-time stateful",
      "aria-expanded": "false",
      onclick: toggleList,
    },
    icon("schedule"),
    h("span", { class: "due-time-text" }, "Time"),
    timeLabel,
    icon("arrow_drop_down")
  );

  // The calendar and the time list swap places, so the popover stays compact.
  function toggleList() {
    const opening = timeButton.getAttribute("aria-expanded") !== "true";
    timeButton.setAttribute("aria-expanded", String(opening));
    calendarPane.classList.toggle("closed", opening);
    listPane.classList.toggle("closed", !opening);

    if (opening) {
      const list = createTimeList({
        value: chosen,
        onSelect: (date) => {
          chosen = new Date(date);
          timeLabel.textContent = formatTime(chosen);
          toggleList();
        },
      });
      listInner.replaceChildren(list.el);
      list.scrollToSelected();
      list.focusSelected();
    } else {
      setTimeout(() => {
        if (listPane.classList.contains("closed")) listInner.replaceChildren();
      }, 300);
    }
    setTimeout(() => popover?.reposition(), 320);
  }

  // One-click presets save at once; the calendar below stays for anything else.
  const at = (daysAhead, hours) => withTime(addDays(startOfDay(now), daysAhead), { hours, minutes: 0 });
  const toNextMonday = ((8 - now.getDay()) % 7) || 7;
  const presets = [
    { label: "This evening", date: at(0, 18) },
    { label: "Tomorrow", date: at(1, 9) },
    { label: "Next Monday", date: at(toNextMonday, 9) },
  ].filter((p) => p.date > now);
  const presetRow = h(
    "div",
    { class: "due-presets" },
    presets.map((p) =>
      h(
        "button",
        {
          type: "button",
          class: "chip small stateful",
          onclick: () => {
            onSave?.(new Date(p.date));
            popover?.close();
          },
        },
        p.label
      )
    )
  );

  const content = h(
    "div",
    { class: "due-picker" },
    presets.length ? presetRow : null,
    calendarPane,
    h("hr", { class: "divider" }),
    timeButton,
    listPane,
    h(
      "div",
      { class: "due-actions" },
      h("button", { type: "button", class: "btn text stateful", onclick: () => popover?.close() }, "Cancel"),
      h(
        "button",
        {
          type: "button",
          class: "btn filled stateful",
          onclick: () => {
            onSave?.(new Date(chosen));
            popover?.close();
          },
        },
        "Save"
      )
    )
  );

  popover = openPopover({ anchor, content, className: "due-popover" });
  return popover;
}
