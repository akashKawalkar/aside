// shared/ui/datetime-utils.js — pure date/time helpers (no DOM).
// 12-hour clock, weeks start on Monday, local time zone.

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];
const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

export const MONTH_LABELS = MONTHS.map((m) => m[0].toUpperCase() + m.slice(1));
export const WEEKDAY_HEADERS = ["M", "T", "W", "T", "F", "S", "S"]; // Monday first

const pad2 = (n) => String(n).padStart(2, "0");

// ---------- basic date arithmetic ----------

export function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addDays(date, n) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + n,
    date.getHours(), date.getMinutes(), date.getSeconds(), date.getMilliseconds());
}

export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
}

/** Whole calendar days from b to a (positive when a is later). */
export function dayDiff(a, b) {
  return Math.round((startOfDay(a) - startOfDay(b)) / 86_400_000);
}

/** The date part of `day` with the time of `time` ({hours, minutes}). */
export function withTime(day, { hours, minutes }) {
  return new Date(day.getFullYear(), day.getMonth(), day.getDate(), hours, minutes, 0, 0);
}

/** 42 local dates (6 weeks) covering the month, Monday first. */
export function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7; // Monday = 0
  return Array.from({ length: 42 }, (_, i) => new Date(year, month, 1 - offset + i));
}

/** "2026-10-06" in local time — what the schedule API's ?date= expects. */
export function toYmd(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

// ---------- ISO with the local offset ----------

export function toIsoOffset(date) {
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const abs = Math.abs(offset);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}` +
    `T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}` +
    `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`
  );
}

// ---------- time ----------

/** "9:30 AM" */
export function formatTime(date) {
  const h = date.getHours();
  const m = date.getMinutes();
  return `${h % 12 === 0 ? 12 : h % 12}:${pad2(m)} ${h < 12 ? "AM" : "PM"}`;
}

/** "9:00 – 10:00 AM" (meridiem shown once when both ends share it). */
export function formatTimeRange(start, end) {
  const a = formatTime(start);
  const b = formatTime(end);
  const [aTime, aMer] = a.split(" ");
  const [, bMer] = b.split(" ");
  return aMer === bMer && sameDay(start, end) ? `${aTime} – ${b}` : `${a} – ${b}`;
}

/**
 * Parse typed time text into { hours (0-23), minutes } or null.
 * Accepts "6pm", "6:30 pm", "6.30pm", "18:00", "630", "1830", "6", "noon", "midnight".
 * A bare 1-12 with no am/pm takes `assumePm` when given (the am/pm of the
 * value being replaced); with nothing to go on, 1-6 and 12 mean pm, 7-11 am.
 */
export function parseTime(text, { assumePm = null } = {}) {
  if (typeof text !== "string") return null;
  const t = text
    .trim()
    .toLowerCase()
    .replace(/([ap])\.m\.?/g, "$1m") // "p.m." -> "pm"
    .replace(/\./g, ":") // "6.30" -> "6:30"
    .replace(/:+$/, "");

  if (t === "noon") return { hours: 12, minutes: 0 };
  if (t === "midnight") return { hours: 0, minutes: 0 };

  const match = /^(\d{1,2})(?::?(\d{2}))?\s*(a|p)?(?:m)?$/.exec(t.replace(/\s+/g, " "));
  if (!match) return null;

  let hours = Number(match[1]);
  const minutes = match[2] === undefined ? 0 : Number(match[2]);
  const meridiem = match[3] ?? null;

  if (minutes > 59) return null;

  if (meridiem) {
    if (hours < 1 || hours > 12) return null;
    hours = hours % 12 + (meridiem === "p" ? 12 : 0);
    return { hours, minutes };
  }

  if (hours > 23) return null;
  if (hours === 0 || hours > 12) return { hours, minutes }; // unambiguous 24-hour

  const pm = assumePm ?? (hours <= 6 || hours === 12);
  return { hours: hours % 12 + (pm ? 12 : 0), minutes };
}

/** "30 mins", "1 hr", "1.5 hrs", "1 hr 10 mins" */
export function formatDuration(ms) {
  const total = Math.round(ms / 60_000);
  if (total < 60) return `${total} min${total === 1 ? "" : "s"}`;

  const hours = Math.floor(total / 60);
  const rest = total % 60;

  if (rest === 0) return `${hours} hr${hours === 1 ? "" : "s"}`;
  if (rest === 30) return `${hours}.5 hrs`;
  return `${hours} hr ${rest} min${rest === 1 ? "" : "s"}`;
}

/** Slots for a time list: { minutes } from 0 to 1425 in `step` steps. */
export function timeSlots(step = 15) {
  return Array.from({ length: Math.floor(1440 / step) }, (_, i) => i * step);
}

// ---------- dates ----------

/** "Tuesday, October 6" (year added when it is not `now`'s year). */
export function formatDateLong(date, now = new Date()) {
  const weekday = WEEKDAYS[date.getDay()];
  const base = `${weekday[0].toUpperCase()}${weekday.slice(1)}, ${MONTH_LABELS[date.getMonth()]} ${date.getDate()}`;
  return date.getFullYear() === now.getFullYear() ? base : `${base}, ${date.getFullYear()}`;
}

/** "Mon, Oct 5" (year added when it is not `now`'s year). */
export function formatDate(date, now = new Date()) {
  const weekday = WEEKDAYS[date.getDay()].slice(0, 3);
  const month = MONTH_LABELS[date.getMonth()].slice(0, 3);
  const base = `${weekday[0].toUpperCase()}${weekday.slice(1)}, ${month} ${date.getDate()}`;
  return date.getFullYear() === now.getFullYear() ? base : `${base}, ${date.getFullYear()}`;
}

function monthIndex(word) {
  const w = word.toLowerCase().replace(/\.$/, "");
  if (w.length < 3) return -1;
  if (w === "sept") return 8;
  return MONTHS.findIndex((m) => m === w || m.startsWith(w.slice(0, 3)) && m.startsWith(w));
}

function validDate(year, month, day) {
  const d = new Date(year, month, day);
  return d.getFullYear() === year && d.getMonth() === month && d.getDate() === day ? d : null;
}

/**
 * Parse typed date text into a local-midnight Date, or null.
 * "today", "tomorrow", "yesterday", "fri", "Oct 8", "8 Oct 2026",
 * "Mon, Oct 5", "2026-10-08", "08/10/2026" (day first), "8/10".
 */
export function parseDate(text, now = new Date()) {
  if (typeof text !== "string") return null;
  let t = text.trim().toLowerCase();
  if (!t) return null;

  const today = startOfDay(now);

  if (t === "today") return today;
  if (t === "tomorrow" || t === "tmr" || t === "tom") return addDays(today, 1);
  if (t === "yesterday") return addDays(today, -1);

  // Bare weekday: the next one (today counts).
  const weekdayOnly = WEEKDAYS.findIndex((d) => t.length >= 3 && d.startsWith(t));
  if (weekdayOnly >= 0) {
    return addDays(today, (weekdayOnly - today.getDay() + 7) % 7);
  }

  // Drop a leading weekday ("mon, oct 5").
  t = t.replace(/^(mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+/, "");

  let m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(t);
  if (m) return validDate(Number(m[1]), Number(m[2]) - 1, Number(m[3]));

  m = /^(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?$/.exec(t);
  if (m) {
    let year = m[3] === undefined ? today.getFullYear() : Number(m[3]);
    if (m[3] !== undefined && m[3].length === 2) year += 2000;
    return validDate(year, Number(m[2]) - 1, Number(m[1]));
  }

  m = /^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$/.exec(t);
  if (m) {
    const month = monthIndex(m[1]);
    if (month < 0) return null;
    return validDate(m[3] ? Number(m[3]) : today.getFullYear(), month, Number(m[2]));
  }

  m = /^(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?(?:,?\s+(\d{4}))?$/.exec(t);
  if (m) {
    const month = monthIndex(m[2]);
    if (month < 0) return null;
    return validDate(m[3] ? Number(m[3]) : today.getFullYear(), month, Number(m[1]));
  }

  return null;
}

/**
 * How a due date reads on a chip: { label: "Today", time: "5:00 PM",
 * text: "Today, 5:00 PM", overdue }.
 */
export function relativeDue(due, now = new Date()) {
  const diff = dayDiff(due, now);
  const label =
    diff === 0 ? "Today" :
    diff === 1 ? "Tomorrow" :
    diff === -1 ? "Yesterday" :
    formatDate(due, now);
  const time = formatTime(due);

  return { label, time, text: `${label}, ${time}`, overdue: due.getTime() < now.getTime() };
}
