// settings/tabs/tables.js — raw table browser.
import { getTable } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { createSelect } from "../../shared/ui/select.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const TABLES = [
  "events", "sessions", "notes",
  "agent_runs", "eval_runs", "persistent_entry", "diff_log", "mark_wrong_log", "tasks", "task_log", "schedule",
];

const tableView = $("table-view");
const tableCount = $("table-count");

const tableSelect = createSelect({
  label: "Table",
  options: TABLES.map((name) => ({ value: name, label: name })),
  value: "events",
  onChange: (name) => loadTable(name),
});
$("table-select-slot").append(tableSelect.el);

function renderTable(columns, rows) {
  if (rows.length === 0) {
    tableView.replaceChildren(emptyState("table_chart", "No rows."));
    return;
  }

  const body = h(
    "tbody",
    {},
    rows.map((row) =>
      h(
        "tr",
        {},
        columns.map((column) => {
          const value = row[column];
          const text = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
          return h("td", { title: text }, text);
        })
      )
    )
  );

  tableView.replaceChildren(
    h(
      "div",
      { class: "data-table-wrap" },
      h(
        "table",
        { class: "data-table" },
        h("thead", {}, h("tr", {}, columns.map((column) => h("th", {}, column)))),
        body
      )
    )
  );
}

export async function loadTable(name = tableSelect.getValue()) {
  showLoading(tableView, 6);
  tableCount.hidden = true;

  const envelope = await getTable(name);

  if (envelope.status === "error") {
    tableView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const rows = envelope.data.rows ?? [];
  tableCount.textContent = `${rows.length} row${rows.length === 1 ? "" : "s"}`;
  tableCount.hidden = false;
  renderTable(envelope.data.columns ?? [], rows);
}
