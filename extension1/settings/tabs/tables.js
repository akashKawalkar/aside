// settings/tabs/tables.js — raw table browser.
import { getTable } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { createSelect } from "../../shared/ui/select.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const TABLES = [
  "events", "sessions", "notes", "tasks", "task_log", "schedule", "schedule_draft", "day_record", "observations",
  "persistent_entry", "diff_log", "compile_log", "llm_trace", "mark_wrong_log", "heartbeat_log", "monitoring_log",
  "agent_runs", "eval_runs",
];
// The events table mixes every device; this narrows it to one. "all" = no filter.
const EVENT_SOURCES = [
  { value: "all", label: "all sources" },
  { value: "laptop", label: "Windows app focus (laptop)" },
  { value: "browser", label: "Chrome tabs (browser)" },
  { value: "android", label: "Android apps (android)" },
];

const tableView = $("table-view");
const tableCount = $("table-count");

const tableSelect = createSelect({
  label: "Table",
  options: TABLES.map((name) => ({ value: name, label: name })),
  value: "events",
  onChange: (name) => loadTable(name),
});
const sourceSelect = createSelect({
  label: "Source",
  options: EVENT_SOURCES,
  value: "all",
  onChange: () => loadTable(),
});
sourceSelect.el.hidden = true;     // only meaningful for the events table
$("table-select-slot").append(tableSelect.el, sourceSelect.el);

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
  sourceSelect.el.hidden = name !== "events";

  const source = name === "events" ? sourceSelect.getValue() : "all";
  const envelope = await getTable(name, source === "all" ? null : source);

  if (envelope.status === "error") {
    tableView.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const rows = envelope.data.rows ?? [];
  tableCount.textContent = `${rows.length} row${rows.length === 1 ? "" : "s"}`;
  tableCount.hidden = false;
  renderTable(envelope.data.columns ?? [], rows);
}
