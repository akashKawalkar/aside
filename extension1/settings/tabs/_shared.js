// settings/tabs/_shared.js — helpers used by more than one tab.
import { h } from "../../shared/ui/dom.js";
import { icon } from "../../shared/ui/icons.js";

export const $ = (id) => document.getElementById(id);
export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Remove everything inside a container. */
export function empty(node) {
  node.replaceChildren();
}

export function envelopeError(envelope) {
  return envelope.detail || envelope.message || "Request failed.";
}

export function skeletonRows(count = 4) {
  return h(
    "div",
    { class: "skeleton-list" },
    Array.from({ length: count }, () =>
      h("div", { class: "skeleton-row" }, h("span", { class: "skeleton" }), h("span", { class: "skeleton" }))
    )
  );
}

export function emptyState(iconName, text) {
  return h("div", { class: "list-empty" }, icon(iconName), h("span", {}, text));
}

/** Replace a container's children, wrapping its lifetime in a loading skeleton. */
export function showLoading(container, rows) {
  container.replaceChildren(skeletonRows(rows));
}

/** Removal animation for a list row, then run `after`. */
export async function removeRow(row, after) {
  row.style.maxHeight = `${row.offsetHeight}px`;
  requestAnimationFrame(() => row.classList.add("removing"));
  await sleep(280);
  await after();
}
