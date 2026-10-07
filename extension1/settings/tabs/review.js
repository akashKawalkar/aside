// settings/tabs/review.js — the latest review, today's preview, and the email settings.
import { getReview, previewReview, saveReviewSettings } from "../../shared/api.js";
import { h } from "../../shared/ui/dom.js";
import { showSnackbar } from "../../shared/ui/snackbar.js";
import { createSelect } from "../../shared/ui/select.js";
import { createDraftSection } from "../../shared/ui/schedule-draft.js";
import { createTimeInput } from "../../shared/ui/datetime-field.js";
import { formatDateLong, formatTime, formatTimeRange } from "../../shared/ui/datetime-utils.js";
import { $, envelopeError, emptyState, showLoading } from "./_shared.js";

const reviewView = $("review-view");
const reviewToggle = $("review-email-toggle");
const reviewRecipient = $("review-recipient");
// "Draft tomorrow", with the reasons the panel leaves out. Hidden unless it has something to offer.
const draftGroup = $("review-draft-group");
const draftSection = createDraftSection({ showReasons: true, onVisible: (visible) => (draftGroup.hidden = !visible) });
$("review-draft-card").append(draftSection.el);

let reviewSettings = { email_enabled: false, frequency: "daily", recipient: "", time: "21:30" };

const pad = (n) => String(n).padStart(2, "0");

function timeOfDay(hhmm) {
  const [hours, minutes] = hhmm.split(":").map(Number);
  const date = new Date();
  date.setHours(hours, minutes, 0, 0);
  return date;
}

const frequencySelect = createSelect({
  label: "How often",
  options: [
    { value: "daily", label: "Every night" },
    { value: "weekly", label: "Weekly (Sunday night)" },
    { value: "monthly", label: "Monthly (last night of the month)" },
  ],
  value: "daily",
  onChange: () => persistReviewSettings(),
});
$("review-frequency-slot").append(frequencySelect.el);

let reviewTimeValue = timeOfDay(reviewSettings.time);
let reviewTime = null;

function mountReviewTime() {
  reviewTime = createTimeInput({
    value: reviewTimeValue,
    onChange: (date) => {
      reviewTimeValue = date;
      persistReviewSettings();
    },
  });
  $("review-time-slot").replaceChildren(reviewTime.el);
}
mountReviewTime();

function readReviewForm() {
  return {
    email_enabled: reviewToggle.checked,
    frequency: frequencySelect.getValue(),
    recipient: reviewRecipient.value.trim(),
    time: `${pad(reviewTimeValue.getHours())}:${pad(reviewTimeValue.getMinutes())}`,
  };
}

function paintReviewForm(settings) {
  reviewSettings = settings;
  reviewToggle.checked = settings.email_enabled;
  reviewRecipient.value = settings.recipient;
  frequencySelect.setValue(settings.frequency);
  reviewTimeValue = timeOfDay(settings.time);
  mountReviewTime();
}

async function persistReviewSettings() {
  const envelope = await saveReviewSettings(readReviewForm());

  if (envelope.status === "error") {
    paintReviewForm(reviewSettings); // nothing changed, so show that
    showSnackbar(envelopeError(envelope));
    return;
  }
  reviewSettings = envelope.data;
}

reviewToggle.addEventListener("change", persistReviewSettings);
reviewRecipient.addEventListener("change", persistReviewSettings);

function duration(seconds) {
  const total = Math.round(Math.abs(seconds) / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (hours && minutes) return `${hours}h ${minutes}m`;
  return hours ? `${hours}h` : `${minutes}m`;
}

function signedDuration(seconds) {
  if (Math.round(seconds / 60) === 0) return "on plan";
  return `${seconds > 0 ? "+" : "-"}${duration(seconds)}`;
}

function statRows(stats) {
  const rows = [];
  if (stats.wall_clock_seconds) rows.push(["Wall-clock time", duration(stats.wall_clock_seconds)]);
  if (stats.difference_seconds !== null) {
    rows.push(["Plan", `${duration(stats.planned_seconds)} (${signedDuration(stats.difference_seconds)})`]);
  }
  if (stats.tasks_completed) rows.push(["Tasks completed", String(stats.tasks_completed)]);
  if (stats.notes) rows.push(["Notes", String(stats.notes)]);
  return rows;
}

function reviewTimeline(rows) {
  const column = (kind, title) =>
    h(
      "div",
      { class: "review-col" },
      h("div", { class: "review-col-title" }, title),
      ...rows
        .filter((row) => row.kind === kind)
        .map((row) =>
          h(
            "div",
            { class: `review-slot ${kind}` },
            h("span", { class: "review-slot-time" }, formatTimeRange(new Date(row.start), new Date(row.end))),
            h("span", {}, row.label)
          )
        )
    );
  return h("div", { class: "review-timeline" }, column("planned", "Planned"), column("actual", "Actual"));
}

function renderReview(review) {
  const { data } = review;
  const [year, month, day] = data.date.split("-").map(Number);
  const nodes = [h("h3", { class: "review-title" }, formatDateLong(new Date(year, month - 1, day)))];

  if (data.quiet) nodes.push(h("p", { class: "supporting" }, "Quiet day. Nothing recorded."));

  const stats = statRows(data.stats);
  if (stats.length) {
    nodes.push(
      h(
        "table",
        { class: "data-table review-stats" },
        h("tbody", {}, ...stats.map(([name, value]) => h("tr", {}, h("td", {}, name), h("td", {}, value))))
      )
    );
  }

  if (data.completed_tasks.length) {
    nodes.push(
      h("h4", { class: "review-sub" }, "Done"),
      h("ul", { class: "review-list" }, ...data.completed_tasks.map((text) => h("li", {}, text)))
    );
  }

  if (data.timeline.length) nodes.push(h("h4", { class: "review-sub" }, "Timeline"), reviewTimeline(data.timeline));

  const { schedule, tasks } = data.tomorrow;
  if (schedule.length || tasks.length) {
    nodes.push(
      h("h4", { class: "review-sub" }, "Tomorrow"),
      h(
        "ul",
        { class: "review-list" },
        ...schedule.map((entry) =>
          h("li", {}, `${formatTimeRange(new Date(entry.start), new Date(entry.end))}  ${entry.title}`)
        ),
        ...tasks.map((task) => h("li", {}, `Due ${formatTime(new Date(task.due))}: ${task.text}`))
      )
    );
  }

  reviewView.replaceChildren(...nodes);
}

export async function loadReview() {
  draftSection.refresh();
  showLoading(reviewView, 3);
  const envelope = await getReview();

  if (envelope.status === "error") {
    reviewView.replaceChildren(emptyState("sync_problem", envelopeError(envelope)));
    return;
  }

  paintReviewForm(envelope.data.settings);

  if (envelope.data.review) renderReview(envelope.data.review);
  else {
    reviewView.replaceChildren(
      emptyState("notes", "No review yet. The first one is written tonight, or preview today below.")
    );
  }
}

$("review-preview").addEventListener("click", async () => {
  const button = $("review-preview");
  button.disabled = true;
  const envelope = await previewReview();
  button.disabled = false;

  if (envelope.status === "error") {
    showSnackbar(envelopeError(envelope));
    return;
  }
  renderReview(envelope.data);
  showSnackbar("This is today so far. It isn't saved or emailed.");
});
