// settings/tabs/general.js — the monitoring switch.
import { getMonitoring, getPrivacy } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { $, envelopeError } from "./_shared.js";

const monitoringToggle = $("toggle-monitoring");

const list = (items) => (items.length ? items.join(", ") : "none");

async function loadPrivacy() {
  const view = $("privacy-view");
  const res = await getPrivacy();
  if (res.status !== "ok") {
    view.replaceChildren(h("div", { class: "supporting" }, `Couldn't load: ${envelopeError(res)}`));
    return;
  }
  const p = res.data;
  const row = (label, value) => h("div", { class: "supporting" }, `${label}: ${value}`);
  view.replaceChildren(
    row("Never sent (sources)", list(p.deny_sources)),
    row("Never sent (tags)", list(p.deny_tags)),
    row("Unattended model calls", p.background_enabled ? "on" : "off"),
    row("Model calls today", `${p.calls_today} of ${p.daily_call_cap}`)
  );
}

export async function loadGeneralSettings() {
  loadPrivacy();
  const state = await getMonitoring();
  if (state.status === "ok") {
    monitoringToggle.checked = state.data.enabled;
  } else {
    // Service unreachable: show the last known state, and say so.
    const cached = await chrome.storage.local.get("monitoring_enabled");
    monitoringToggle.checked = cached.monitoring_enabled ?? true;
    showSnackbar("Can't reach the local service, so monitoring may not be up to date.");
  }
}

monitoringToggle.addEventListener("change", async () => {
  const wanted = monitoringToggle.checked;
  monitoringToggle.disabled = true;

  let result;
  try {
    result = await chrome.runtime.sendMessage({ type: "set-monitoring", enabled: wanted });
  } catch {
    result = { error: "Couldn't reach the extension's background worker." };
  }
  monitoringToggle.disabled = false;

  if (result?.error) {
    monitoringToggle.checked = !wanted; // nothing changed, so show that
    showSnackbar(result.error);
    return;
  }

  monitoringToggle.checked = result.enabled;
  showSnackbar(result.enabled ? "Monitoring resumed" : "Monitoring paused");
});
