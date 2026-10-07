// settings/tabs/ring-log.js — connection log, kept client-side in chrome.storage.
import { h } from "../../shared/ui/dom.js";
import { $, emptyState } from "./_shared.js";

const ringLogView = $("ring-log-view");

export async function loadRingLog() {
  const { ring_events: events = [] } = await chrome.storage.local.get("ring_events");

  if (events.length === 0) {
    ringLogView.replaceChildren(emptyState("sync_problem", "Nothing logged yet."));
    return;
  }

  ringLogView.replaceChildren(
    ...events.map((event, i) =>
      h(
        "div",
        { class: "card log-card", style: { "--i": String(Math.min(i, 12)) } },
        h(
          "div",
          { class: "log-head" },
          h("span", { class: `chip small ${event.status === "ambiguous" ? "warn" : event.status}` }, event.status),
          h("span", { class: "when" }, new Date(event.at).toLocaleString())
        ),
        h("div", { class: "log-detail" }, event.detail || event.message || "(no detail)")
      )
    )
  );
}
