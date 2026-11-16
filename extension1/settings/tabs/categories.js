// settings/tabs/categories.js — weekly Sunday review of LLM category suggestions and rule management.
import {
  approveCategoryRule,
  createCategoryRule,
  deleteCategoryRule,
  getCategoryRules,
  updateCategoryRule,
} from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { $, emptyState, envelopeError, showLoading } from "./_shared.js";

const view = $("categories-view");

const ALL_CATEGORIES = [
  "coding", "study", "ai_assist", "work_admin", "comms", "social", "media",
  "job_search", "networking", "wellbeing", "shopping", "other", "system", "idle",
];

function categorySelect(current, onChange) {
  const select = h("select", {
    class: "category-select",
    onchange: (e) => onChange(e.target.value),
  }, ...ALL_CATEGORIES.map((cat) =>
    h("option", { value: cat, selected: cat === current ? "selected" : undefined }, cat)
  ));
  return select;
}

function reviewCard(row) {
  const target = row.app ? `App: ${row.app}` : row.domain ? `Domain: ${row.domain}` : row.label;
  const source = row.source ? ` (${row.source})` : "";

  const approveBtn = h("button", {
    class: "btn filled-tonal stateful",
    type: "button",
    onclick: async () => {
      const res = await approveCategoryRule(row.id);
      if (res.status === "ok") await loadCategories();
      else alert(envelopeError(res));
    },
  }, "Approve");

  const rejectBtn = h("button", {
    class: "btn text stateful",
    type: "button",
    onclick: async () => {
      const res = await deleteCategoryRule(row.id);
      if (res.status === "ok") await loadCategories();
      else alert(envelopeError(res));
    },
  }, "Reject");

  const select = categorySelect(row.category, async (newCat) => {
    const res = await updateCategoryRule(row.id, { category: newCat });
    if (res.status === "ok") await loadCategories();
    else alert(envelopeError(res));
  });

  return h("div", { class: "card setting-card" },
    h("div", { class: "setting-row" },
      h("div", { class: "setting-text" },
        h("div", { class: "title-medium" }, row.label),
        h("div", { class: "supporting" }, `${target}${source}`),
        h("div", { class: "supporting muted" }, `Suggested category: ${row.category}`)
      ),
      h("div", { class: "draft-actions", style: "display: flex; gap: 8px; align-items: center;" },
        select,
        approveBtn,
        rejectBtn
      )
    )
  );
}

function ruleCard(row) {
  const target = row.app ? `App: ${row.app}` : row.domain ? `Domain: ${row.domain}` : "";
  const titleHas = (row.title_has && row.title_has.length) ? `Title has: ${row.title_has.join(", ")}` : "";
  const details = [target, titleHas, row.source ? `Source: ${row.source}` : ""].filter(Boolean).join(" · ");

  const deleteBtn = h("button", {
    class: "btn text stateful",
    type: "button",
    onclick: async () => {
      if (!confirm(`Delete rule "${row.label}"?`)) return;
      const res = await deleteCategoryRule(row.id);
      if (res.status === "ok") await loadCategories();
      else alert(envelopeError(res));
    },
  }, "Delete");

  const select = categorySelect(row.category, async (newCat) => {
    const res = await updateCategoryRule(row.id, { category: newCat });
    if (res.status === "ok") await loadCategories();
    else alert(envelopeError(res));
  });

  return h("div", { class: "card setting-card" },
    h("div", { class: "setting-row" },
      h("div", { class: "setting-text" },
        h("div", { class: "title-medium" }, row.label),
        details ? h("div", { class: "supporting" }, details) : null,
        h("div", { class: "supporting muted" }, `Origin: ${row.origin} · Priority: ${row.priority}`)
      ),
      h("div", { class: "draft-actions", style: "display: flex; gap: 8px; align-items: center;" },
        h("span", { class: `chip small ${row.origin === "llm" ? "warn" : "ok"}` }, row.category),
        select,
        deleteBtn
      )
    )
  );
}

function addRuleForm() {
  const appInput = h("input", { type: "text", class: "text-field", placeholder: "e.g. Code.exe or obsidian" });
  const domainInput = h("input", { type: "text", class: "text-field", placeholder: "e.g. github.com" });
  const labelInput = h("input", { type: "text", class: "text-field", placeholder: "Label name", required: "required" });
  const catSelect = categorySelect("coding", () => {});

  const addBtn = h("button", {
    class: "btn filled stateful",
    type: "button",
    onclick: async () => {
      const label = labelInput.value.trim();
      const app = appInput.value.trim() || undefined;
      const domain = domainInput.value.trim() || undefined;
      const category = catSelect.value;
      if (!label) {
        alert("Label is required.");
        return;
      }
      if (!app && !domain) {
        alert("Provide either an app or a domain.");
        return;
      }
      const res = await createCategoryRule({ category, label, app, domain });
      if (res.status === "ok") await loadCategories();
      else alert(envelopeError(res));
    },
  }, "Add Rule");

  return h("div", { class: "card setting-card" },
    h("div", { class: "title-medium", style: "margin-bottom: 8px;" }, "Add Custom Rule"),
    h("div", { style: "display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 8px; margin-bottom: 8px;" },
      h("label", {}, h("span", { class: "supporting" }, "Label: "), labelInput),
      h("label", {}, h("span", { class: "supporting" }, "Category: "), catSelect),
      h("label", {}, h("span", { class: "supporting" }, "App: "), appInput),
      h("label", {}, h("span", { class: "supporting" }, "Domain: "), domainInput)
    ),
    addBtn
  );
}

export async function loadCategories() {
  showLoading(view, 3);
  const response = await getCategoryRules();
  if (response.status === "error") {
    view.replaceChildren(emptyState("error", `Couldn't load: ${envelopeError(response)}`));
    return;
  }
  const rules = response.data.rules ?? [];
  const pending = rules.filter((r) => r.origin === "llm" && !r.reviewed);
  const active = rules.filter((r) => r.reviewed || r.origin !== "llm");

  const nodes = [];

  // Sunday Review Section
  nodes.push(h("div", { class: "section-head", style: "margin-top: 16px;" },
    h("h3", {}, `Weekly Review (${pending.length})`),
    h("p", { class: "supporting" }, "Auto-accepted category suggestions from nightly digests. Approve, change, or reject them.")
  ));

  if (pending.length) {
    nodes.push(...pending.map(reviewCard));
  } else {
    nodes.push(h("div", { class: "card setting-card" },
      h("div", { class: "supporting" }, "No suggestions pending review. All rules are verified.")
    ));
  }

  // Add rule form
  nodes.push(h("div", { style: "margin-top: 24px;" }, addRuleForm()));

  // Active rules section
  nodes.push(h("div", { class: "section-head", style: "margin-top: 24px;" },
    h("h3", {}, `Active Rules (${active.length})`),
    h("p", { class: "supporting" }, "All mapped rules for laptop and phone activity.")
  ));

  if (active.length) {
    nodes.push(...active.map(ruleCard));
  } else {
    nodes.push(emptyState("category", "No active rules found."));
  }

  view.replaceChildren(...nodes);
}
