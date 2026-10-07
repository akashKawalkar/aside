// shared/ui/snackbar.js — Google-style snackbars, stacked bottom-left.

import { h } from "./dom.js";

const MAX_VISIBLE = 3;
let host = null;

function getHost() {
  if (!host || !host.isConnected) {
    host = h("div", { class: "snackbar-host", role: "region", "aria-label": "Notifications" });
    document.body.appendChild(host);
  }
  return host;
}

/**
 * showSnackbar("Couldn't save task", { action: "Retry", onAction: fn })
 * Returns a function that dismisses it. Auto-dismisses after `duration` ms.
 */
export function showSnackbar(message, { action = "Dismiss", onAction, duration = 5000 } = {}) {
  const container = getHost();

  // Drop the oldest so a burst of errors never fills the panel.
  while (container.children.length >= MAX_VISIBLE) {
    container.firstElementChild.remove();
  }

  let timer = null;

  const dismiss = () => {
    clearTimeout(timer);
    if (!bar.isConnected || bar.classList.contains("leaving")) return;
    bar.classList.add("leaving");
    bar.addEventListener("animationend", () => bar.remove(), { once: true });
    setTimeout(() => bar.remove(), 400);
  };

  const bar = h(
    "div",
    { class: "snackbar", role: "status" },
    h("span", { class: "message" }, message),
    action
      ? h(
          "button",
          {
            class: "btn text stateful",
            onclick: () => {
              onAction?.();
              dismiss();
            },
          },
          action
        )
      : null
  );

  container.appendChild(bar);
  timer = setTimeout(dismiss, duration);
  return dismiss;
}
