// settings/tabs/general.js — the monitoring switch.
import { getMonitoring } from "../../shared/api.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { $ } from "./_shared.js";

const monitoringToggle = $("toggle-monitoring");

export async function loadGeneralSettings() {
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
