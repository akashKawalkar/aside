// settings/tabs/schedule.js — one day of the schedule (Google Calendar style).
import { getScheduleDay, updateScheduleEntry, deleteScheduleEntry } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { openPopover } from "../../shared/ui/popover.js";
import { createDatePicker } from "../../shared/ui/datepicker.js";
import { createEventEditor } from "../../shared/ui/event-editor.js";
import {
  addDays,
  formatDateLong,
  formatTime,
  formatTimeRange,
  startOfDay,
  toIsoOffset,
  toYmd,
} from "../../shared/ui/datetime-utils.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const scheduleView = $("schedule-view");
const scheduleDateButton = $("schedule-date");
let scheduleDay = startOfDay(new Date());
let schedulePicker = null;

function entryBlock(entry) {
  const start = new Date(entry.start_at);
  const end = new Date(entry.end_at);
  const now = Date.now();
  const isNow = start.getTime() <= now && end.getTime() > now;

  const block = h(
    "div",
    { class: `event stateful${isNow ? " now" : ""}`, tabindex: "0", title: "Click to edit" },
    h(
      "div",
      { class: "event-body" },
      h("span", { class: "event-title" }, entry.title),
      h("span", { class: "event-time" }, formatTimeRange(start, end))
    ),
    isNow ? h("span", { class: "event-badge now" }, "Now") : null,
    entry.origin === "generated"
      ? h("span", { class: "event-badge generated" }, entry.edited_by_user ? "Generated, edited" : "Generated")
      : null
  );

  const open = () => openEditor(block, entry);
  block.addEventListener("click", open);
  block.addEventListener("keydown", (e) => {
    if (e.target === block && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      open();
    }
  });

  return block;
}

function openEditor(block, entry) {
  block.className = "event editing";
  block.removeAttribute("title");
  block.removeAttribute("tabindex");
  block.replaceChildren();

  const editor = createEventEditor({
    entry,
    onCancel: () => loadSchedule({ quiet: true }),
    onSave: async ({ title, start, end }) => {
      const envelope = await updateScheduleEntry(entry.id, title, toIsoOffset(start), toIsoOffset(end));
      if (envelope.status === "error") {
        showSnackbar(envelopeError(envelope));
        return false;
      }
      await loadSchedule({ quiet: true });
      return true;
    },
    onDelete: async () => {
      const envelope = await deleteScheduleEntry(entry.id);
      if (envelope.status === "error") {
        showSnackbar(envelopeError(envelope));
        return false;
      }
      await loadSchedule({ quiet: true });
      showSnackbar("Entry deleted");
      return true;
    },
  });

  block.append(editor.el);
  editor.focus();
}

export async function loadSchedule({ quiet = false } = {}) {
  scheduleDateButton.textContent = formatDateLong(scheduleDay);
  if (!quiet) showLoading(scheduleView, 4);

  const envelope = await getScheduleDay(toYmd(scheduleDay));

  if (envelope.status === "error") {
    scheduleView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const entries = envelope.data?.entries ?? [];
  if (entries.length === 0) {
    scheduleView.replaceChildren(emptyState("event", "Nothing scheduled this day."));
    return;
  }

  scheduleView.replaceChildren(
    h(
      "ul",
      { class: "sched-list" },
      entries.map((entry, i) => {
        const start = new Date(entry.start_at);
        const end = new Date(entry.end_at);
        const row = h(
          "li",
          { class: "sched-row", style: { "--i": String(i) } },
          h("div", { class: "sched-time" }, h("div", {}, formatTime(start)), h("div", {}, formatTime(end)))
        );
        row.append(entryBlock(entry));
        return row;
      })
    )
  );
}

export function setScheduleDay(day) {
  scheduleDay = startOfDay(day);
  loadSchedule();
}

$("schedule-prev").addEventListener("click", () => setScheduleDay(addDays(scheduleDay, -1)));
$("schedule-next").addEventListener("click", () => setScheduleDay(addDays(scheduleDay, 1)));
$("schedule-today").addEventListener("click", () => setScheduleDay(new Date()));

scheduleDateButton.addEventListener("click", () => {
  if (schedulePicker) {
    schedulePicker.close();
    return;
  }

  const picker = createDatePicker({
    value: scheduleDay,
    onSelect: (day) => {
      schedulePicker?.close();
      setScheduleDay(day);
    },
  });

  schedulePicker = openPopover({
    anchor: scheduleDateButton,
    content: picker.el,
    className: "dp-popover",
    onClose: () => {
      schedulePicker = null;
    },
  });
});
