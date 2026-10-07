// settings/tabs/skills.js — hand-written instructions the agent loads by rule. Read-only here; edit through the API.
import { api } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { $, emptyState, envelopeError, showLoading } from "./_shared.js";

export async function loadSkills() {
  const container = $("skills-list");
  if (!container) return;
  showLoading(container, 2);

  const envelope = await api.get("/skills");
  if (envelope.status !== "ok") {
    container.replaceChildren(emptyState("sync_problem", `Couldn't load skills: ${envelopeError(envelope)}`));
    return;
  }

  const skills = envelope.data.skills ?? [];
  if (!skills.length) {
    container.replaceChildren(emptyState("lightbulb", "No skills yet."));
    return;
  }

  container.replaceChildren(
    ...skills.map((s) =>
      h(
        "div",
        { class: "card setting-card" },
        h(
          "div",
          { class: "setting-row" },
          h(
            "div",
            { class: "setting-text" },
            h("div", { class: "title-medium" }, s.name),
            h("div", { class: "supporting" }, `Use when: ${s.use_when || "—"}${s.affects ? ` · Affects: ${s.affects}` : ""}`),
            h("div", { class: "supporting" }, `Used ${s.usage_count} time${s.usage_count === 1 ? "" : "s"} · corrected ${s.correction_count}`)
          )
        ),
        h("div", { class: "setting-row" }, h("pre", { class: "supporting", style: { whiteSpace: "pre-wrap", fontFamily: "var(--font-mono)", fontSize: "13px", margin: "0" } }, s.body))
      )
    )
  );
}
