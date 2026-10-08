// settings/tabs/observations.js — code-derived patterns and their evidence.
import { deleteObservation, getObservations } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { $, emptyState, envelopeError, showLoading } from "./_shared.js";

const view = $("observations-view");

const dateText = (value) => value ? new Date(`${value}T00:00:00`).toLocaleDateString() : "unknown";

function card(row) {
  const evidence = row.evidence ?? {};
  const first = dateText(row.first_seen);
  const last = dateText(row.last_seen);
  const days = evidence.occurrence_days ?? evidence.days ?? [];
  const remove = h("button", {
    class: "btn text stateful",
    type: "button",
    onclick: async () => {
      const result = await deleteObservation(row.id);
      if (result.status === "ok") await loadObservations();
      else view.prepend(h("div", { class: "supporting error-text" }, envelopeError(result)));
    },
  }, "Delete");
  return h("div", { class: "card setting-card" },
    h("div", { class: "setting-row" },
      h("div", { class: "setting-text" },
        h("div", { class: "title-medium" }, row.text),
        h("div", { class: "supporting" }, `${row.occurrences} occurrence${row.occurrences === 1 ? "" : "s"} · ${first}–${last}`),
        days.length ? h("div", { class: "supporting" }, `Recorded: ${days.join(", ")}`) : null
      ),
      h("div", { class: "draft-actions" },
        h("span", { class: "chip small" }, row.kind.replaceAll("_", " ")),
        h("span", { class: `chip small ${row.status === "active" ? "ok" : row.status === "questioned" ? "warn" : ""}`.trim() }, row.status === "questioned" ? "? check" : row.status),
        remove
      )
    )
  );
}

export async function loadObservations() {
  showLoading(view, 3);
  const response = await getObservations();
  if (response.status === "error") {
    view.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(response)}`));
    return;
  }
  const rows = response.data.observations ?? [];
  if (!rows.length) {
    view.replaceChildren(emptyState("event", "No observations yet. They appear after enough repeated data."));
    return;
  }
  view.replaceChildren(...rows.map(card));
}
