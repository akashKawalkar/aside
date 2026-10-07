// settings/tabs/data-gaps.js — month heatmap of tracked time, the gaps inside it, and collector/ingestor health.
import { getDataGaps } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { icon } from "../../shared/ui/icons.js";
import { formatTime } from "../../shared/ui/datetime-utils.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const view = $("gaps-view");
const healthView = $("gaps-health");
const title = $("gaps-title");

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const REASON_CLASS = { paused: "", "computer off or asleep": "warn", "collector not running": "error", "ingestor not running": "error", unexplained: "warn" };

let month = null;        // "YYYY-MM"; null until first load, then the server's current month
let selectedDay = null;  // "YYYY-MM-DD" filter for the gap list

const hours = (seconds) => `${(seconds / 3600).toFixed(1)}h`;
const level = (seconds) => (seconds <= 0 ? 0 : Math.min(4, Math.ceil(seconds / 3600 / 3)));   // 0, then 3h steps up to 4

function shiftMonth(ym, by) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, m - 1 + by, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function ago(seconds) {
  if (seconds === null) return "never";
  if (seconds < 90) return `${seconds}s ago`;
  return seconds < 5400 ? `${Math.round(seconds / 60)}m ago` : `${Math.round(seconds / 3600)}h ago`;
}

function healthChips(health) {
  const process = (name, beat) =>
    h("span", { class: `chip small ${beat.ok ? "ok" : "error"}` }, `${name}: ${beat.ok ? "running" : "not running"} (${ago(beat.age_seconds)})`);
  return [
    process("Collector", health.collector),
    process("Ingestor", health.ingestor),
    h("span", { class: "chip small" }, `Last session: ${health.last_session_end ? new Date(health.last_session_end).toLocaleString() : "none yet"}`),
    h("span", { class: "chip small" }, `Last day record: ${health.last_day_record ?? "none yet"}`),
  ];
}

function heatmap(days) {
  const [year, mon] = month.split("-").map(Number);
  const offset = (new Date(year, mon - 1, 1).getDay() + 6) % 7;   // week starts Monday
  const cells = [
    ...WEEKDAYS.map((d) => h("div", { class: "heat-dow" }, d)),
    ...Array.from({ length: offset }, () => h("div", { class: "heat-cell blank" })),
  ];

  for (const day of days) {
    const tip = day.in_future ? day.date : `${day.date}: ${hours(day.tracked_seconds)} tracked, ${day.gap_count} gap${day.gap_count === 1 ? "" : "s"} (${day.gap_minutes} min)`;
    const cell = h(
      "button",
      {
        type: "button",
        class: ["heat-cell", `lvl-${level(day.tracked_seconds)}`, day.in_future ? "future" : "", selectedDay === day.date ? "selected" : ""].filter(Boolean).join(" "),
        title: tip,
        "aria-label": tip,
        disabled: day.in_future,
        onclick: () => {
          selectedDay = selectedDay === day.date ? null : day.date;
          loadDataGaps({ quiet: true });
        },
      },
      String(Number(day.date.slice(8))),
      day.gap_count ? h("span", { class: "heat-gap" }, String(day.gap_count)) : null
    );
    cells.push(cell);
  }

  const swatch = (n) => h("span", { class: `swatch heat-cell lvl-${n}`, style: { width: "14px", minHeight: "14px", padding: "0" } });
  return h(
    "div",
    {},
    h("div", { class: "heat" }, cells),
    h("div", { class: "heat-legend" }, "Tracked:", swatch(0), "none", swatch(1), swatch(2), swatch(3), swatch(4), "12h+", h("span", { class: "heat-gap", style: { position: "static" } }, "n"), "gaps that day")
  );
}

function gapList(gaps) {
  const shown = selectedDay ? gaps.filter((g) => g.start.startsWith(selectedDay)) : gaps;
  const heading = h("h4", { class: "review-sub" }, selectedDay ? `Gaps on ${selectedDay} (click the day again for all)` : `Gaps (${gaps.length})`);
  if (shown.length === 0) return h("div", {}, heading, emptyState("task_alt", "No gaps in waking hours."));

  return h(
    "div",
    {},
    heading,
    shown.map((g) => {
      const start = new Date(g.start);
      const end = new Date(g.end);
      return h(
        "div",
        { class: "gap-row" },
        h("span", { class: "when" }, `${start.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}  ${formatTime(start)}–${formatTime(end)}`),
        h("span", { class: "len" }, `${g.minutes} min`),
        h("span", { class: `chip small ${REASON_CLASS[g.reason] ?? "warn"}` }, g.reason)
      );
    })
  );
}

export async function loadDataGaps({ quiet = false } = {}) {
  if (!quiet) showLoading(view, 5);
  const envelope = await getDataGaps(month);

  if (envelope.status === "error") {
    view.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(envelope)}`));
    return;
  }

  const data = envelope.data;
  month = data.month;
  title.textContent = new Date(`${month}-01T00:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" });
  healthView.replaceChildren(...healthChips(data.health));
  view.replaceChildren(heatmap(data.days), gapList(data.gaps));
}

function go(by) {
  if (!month) return;
  month = shiftMonth(month, by);
  selectedDay = null;
  loadDataGaps();
}

$("gaps-prev").addEventListener("click", () => go(-1));
$("gaps-next").addEventListener("click", () => go(1));
