// settings/settings.js — navigation and start-up; each tab lives in settings/tabs/.
import { icon } from "../shared/ui/icons.js";
import { installRipple } from "../shared/ui/ripple.js";
import { $ } from "./tabs/_shared.js";
import { loadGeneralSettings } from "./tabs/general.js";
import { loadTasks } from "./tabs/tasks.js";
import { loadTaskHistory } from "./tabs/task-history.js";
import { loadSchedule } from "./tabs/schedule.js";
import { loadReview } from "./tabs/review.js";
import { loadDiffLog } from "./tabs/inspect.js";
import { loadDataGaps } from "./tabs/data-gaps.js";
import { loadContext } from "./tabs/context-viewer.js";
import { loadPersistentFile } from "./tabs/persistent-file.js";
import { loadNotes } from "./tabs/notes.js";
import { loadTable } from "./tabs/tables.js";
import { loadRingLog } from "./tabs/ring-log.js";
import { loadSkills } from "./tabs/skills.js";
import { loadStatements } from "./tabs/statements.js";
import { loadDrafts } from "./tabs/drafts.js";


const shell = $("shell");
const navItems = [...document.querySelectorAll(".nav-item")];
const panels = [...document.querySelectorAll(".tab-panel")];
const navIndicator = $("nav-indicator");

// Tabs that show live data reload every time they are opened.
const tabLoaders = {
  tasks: loadTasks,
  "task-history": loadTaskHistory,
  schedule: loadSchedule,
  review: loadReview,
  drafts: loadDrafts,
  inspect: loadDiffLog,
  context: loadContext,
  "data-gaps": loadDataGaps,
  "persistent-file": loadPersistentFile,
  notes: loadNotes,
  tables: () => loadTable(),
  
  "ring-log": loadRingLog,
  skills: loadSkills,
  statements: loadStatements,

};
let currentTab = "general";

function moveNavIndicator({ animate = true } = {}) {
  const active = navItems.find((item) => item.dataset.tab === currentTab);
  if (!active) return;

  if (!animate) navIndicator.style.transition = "none";
  navIndicator.style.transform = `translateY(${active.offsetTop}px)`;
  if (!animate) {
    void navIndicator.offsetWidth;
    navIndicator.style.transition = "";
  }
}

function activateTab(name, { animateIndicator = true } = {}) {
  if (!panels.some((panel) => panel.id === `tab-${name}`)) name = "general";
  currentTab = name;

  navItems.forEach((item) => {
    const active = item.dataset.tab === name;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  panels.forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${name}`));

  moveNavIndicator({ animate: animateIndicator });
  history.replaceState(null, "", `#${name}`);
  tabLoaders[name]?.();
}

navItems.forEach((item) => item.addEventListener("click", () => activateTab(item.dataset.tab)));
window.addEventListener("hashchange", () => activateTab(location.hash.slice(1)));

$("nav-toggle").addEventListener("click", () => {
  const rail = shell.classList.toggle("rail");
  $("nav-toggle").setAttribute("aria-label", rail ? "Expand navigation" : "Collapse navigation");
  // The indicator's position does not change, but the labels do: re-measure after the layout settles.
  setTimeout(() => moveNavIndicator({ animate: false }), 450);
});

async function init() {
  installRipple();

  document.querySelectorAll("[data-icon]").forEach((node) => node.append(icon(node.dataset.icon)));
  document.querySelectorAll(".switch .thumb").forEach((thumb) => thumb.append(icon("check")));

  if (window.innerWidth < 760) shell.classList.add("rail");

  await loadGeneralSettings();

  activateTab(location.hash.slice(1) || "general", { animateIndicator: false });
  document.fonts?.ready.then(() => moveNavIndicator({ animate: false }));
}

init();
