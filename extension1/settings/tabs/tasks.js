// settings/tabs/tasks.js — pending tasks (Google Tasks style).
import { getTasks, updateTask, completeTask, deleteTask, restoreTask } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { icon, checkTick } from "../../shared/ui/icons.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { openDuePicker } from "../../shared/ui/datetime-field.js";
import { relativeDue, toIsoOffset } from "../../shared/ui/datetime-utils.js";
import { $, envelopeError, emptyState, showLoading, removeRow } from "./_shared.js";

const tasksView = $("tasks-view");

function taskRow(task) {
  const due = task.due_at ? new Date(task.due_at) : null;
  const relative = due ? relativeDue(due) : null;
  let saved = task.text ?? "";

  const checkbox = h("input", { type: "checkbox", "aria-label": `Complete task: ${saved || "untitled"}` });
  const check = h("label", { class: "check stateful" }, checkbox, h("span", { class: "box" }, checkTick()));

  const title = h("input", {
    class: "gt-title",
    type: "text",
    value: saved,
    placeholder: "(untitled)",
    maxlength: "10000",
    "aria-label": "Task title",
  });

  const chip = h(
    "button",
    {
      type: "button",
      class: `chip small stateful${relative?.overdue ? " overdue" : ""}`,
      "aria-label": "Change due date",
      onclick: () =>
        openDuePicker({
          anchor: chip,
          value: due ?? new Date(),
          onSave: async (date) => {
            const envelope = await updateTask(task.id, { due_at: toIsoOffset(date) });
            if (envelope.status === "error") {
              showSnackbar(envelopeError(envelope));
              return;
            }
            await loadTasks({ quiet: true });
          },
        }),
    },
    icon("schedule"),
    relative ? relative.text : "Add due date"
  );

  const remove = h(
    "button",
    { type: "button", class: "icon-btn small stateful gt-delete", "aria-label": "Delete task" },
    icon("delete")
  );

  const row = h(
    "li",
    { class: "gt-row" },
    check,
    h("div", { class: "gt-main" }, title, chip),
    remove
  );

  // Autosave the title on Enter or when focus leaves, like Google Tasks.
  async function commitTitle() {
    const next = title.value.trim();
    if (next === saved.trim()) {
      title.value = saved;
      return;
    }
    if (!next) {
      showSnackbar("A task needs a title.");
      title.value = saved;
      return;
    }

    title.disabled = true;
    const envelope = await updateTask(task.id, { text: next });
    title.disabled = false;

    if (envelope.status === "error") {
      showSnackbar(envelopeError(envelope));
      title.value = saved;
      return;
    }

    saved = next;
    title.value = next;
    row.classList.remove("saved");
    void row.offsetWidth;
    row.classList.add("saved");
  }

  title.addEventListener("blur", commitTitle);
  title.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      title.blur();
    } else if (e.key === "Escape") {
      title.value = saved;
      title.blur();
    }
  });

  checkbox.addEventListener("change", async () => {
    checkbox.disabled = true;
    row.classList.add("done");
    const envelope = await completeTask(task.id);

    if (envelope.status === "error") {
      checkbox.checked = false;
      checkbox.disabled = false;
      row.classList.remove("done");
      showSnackbar(envelopeError(envelope));
      return;
    }

    await removeRow(row, () => loadTasks({ quiet: true }));
    showSnackbar("Task completed", {
      action: "Undo",
      duration: 8000,
      onAction: async () => {
        const undone = await restoreTask(task.id);
        if (undone.status === "error") showSnackbar(envelopeError(undone));
        await loadTasks({ quiet: true });
      },
    });
  });

  remove.addEventListener("click", async () => {
    remove.disabled = true;
    const envelope = await deleteTask(task.id);

    if (envelope.status === "error") {
      remove.disabled = false;
      showSnackbar(envelopeError(envelope));
      return;
    }

    await removeRow(row, () => loadTasks({ quiet: true }));
    showSnackbar("Task deleted");
  });

  return row;
}

export async function loadTasks({ quiet = false } = {}) {
  if (!quiet) showLoading(tasksView, 4);
  const envelope = await getTasks(500);

  if (envelope.status === "error") {
    tasksView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const items = envelope.data?.items ?? [];
  if (items.length === 0) {
    tasksView.replaceChildren(emptyState("task_alt", "No pending tasks. ....golf?"));
    return;
  }

  tasksView.replaceChildren(h("ul", { class: "gt-list" }, items.map(taskRow)));
}
