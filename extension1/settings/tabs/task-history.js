// settings/tabs/task-history.js — completed, deleted, expired and dropped tasks as a timeline.
import { getTaskHistory } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { icon } from "../../shared/ui/icons.js";
import { formatDate, formatTime, relativeDue, startOfDay } from "../../shared/ui/datetime-utils.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const taskHistoryView = $("task-history-view");

const HISTORY_ICONS = { completed: "check_circle", deleted: "delete", expired: "hourglass", dropped: "hourglass" };
const HISTORY_LABELS = { completed: "Completed", deleted: "Deleted", expired: "Expired", dropped: "Dropped" };

function dayHeading(day, now) {
  const diff = Math.round((startOfDay(now) - day) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return formatDate(day, now);
}

export async function loadTaskHistory() {
  showLoading(taskHistoryView, 5);
  const envelope = await getTaskHistory(500);

  if (envelope.status === "error") {
    taskHistoryView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const items = envelope.data?.items ?? [];
  if (items.length === 0) {
    taskHistoryView.replaceChildren(emptyState("history", "No completed, deleted or dropped tasks yet."));
    return;
  }

  const now = new Date();
  const list = h("ul", { class: "hist-list" });
  let lastDay = null;
  let index = 0;

  for (const event of items) {
    const when = new Date(event.created_at);
    const day = startOfDay(when);

    if (lastDay === null || day.getTime() !== lastDay.getTime()) {
      list.append(h("li", { class: "day-head" }, dayHeading(day, now)));
      lastDay = day;
    }

    const snapshot = event.snapshot ?? {};
    const was = snapshot.due_at ? `Was due ${relativeDue(new Date(snapshot.due_at), now).text}` : "";
    const why = snapshot.dropped_reason ?? "";

    list.append(
      h(
        "li",
        { class: `hist-row ${event.action}`, style: { "--i": String(index++) } },
        h("span", { class: "hist-icon" }, icon(HISTORY_ICONS[event.action] ?? "history")),
        h(
          "div",
          { class: "hist-body" },
          h("span", { class: "hist-text" }, snapshot.text || "(untitled)"),
          h("span", { class: "hist-meta" }, [HISTORY_LABELS[event.action] ?? event.action, why, was].filter(Boolean).join(" · "))
        ),
        h("span", { class: "hist-time" }, formatTime(when))
      )
    );
  }

  taskHistoryView.replaceChildren(list);
}
