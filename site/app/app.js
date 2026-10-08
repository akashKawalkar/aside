/* Aside dashboard: read-only, signed in with the same login as the phone app.
   The page never touches tables. It calls one database function (dashboard) and the note search, as the signed-in user.
   Everything from the database is put on the page as text, never as HTML. */
(function () {
  "use strict";
  var CFG = window.ASIDE || {};
  var TZ = "Asia/Kolkata";
  var KEY = "aside_session";

  // ---------- tiny helpers ----------
  function $(id) { return document.getElementById(id); }
  function h(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "class") e.className = attrs[k];
      else if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (k === "style") e.setAttribute("style", attrs[k]);
      else e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c != null) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function parts(iso) {
    var f = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
    var o = {}; f.formatToParts(new Date(iso)).forEach(function (p) { o[p.type] = p.value; });
    return { date: o.year + "-" + o.month + "-" + o.day, h: +o.hour % 24, m: +o.minute };
  }
  function istToday() { return parts(new Date().toISOString()).date; }
  function addDays(ymd, n) { var d = new Date(ymd + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function timeOf(iso) { return new Date(iso).toLocaleTimeString("en-IN", { timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true }).toUpperCase(); }
  function dayLabel(ymd) { return new Date(ymd + "T12:00:00Z").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }); }
  function stamp(iso) { return iso ? dayLabel(parts(iso).date) + ", " + timeOf(iso) : "never"; }
  function ago(iso) {
    if (!iso) return "never";
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return "just now";
    if (s < 5400) return Math.round(s / 60) + " min ago";
    if (s < 129600) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  function dur(sec) {
    sec = Math.round(sec || 0);
    var hh = Math.floor(sec / 3600), mm = Math.round((sec % 3600) / 60);
    if (mm === 60) { hh += 1; mm = 0; }
    return hh ? hh + "h " + pad(mm) + "m" : mm + "m";
  }
  var APPS = { "com.google.android.youtube": "YouTube", "com.google.android.apps.maps": "Maps", "com.spotify.music": "Spotify",
    "com.android.chrome": "Chrome", "com.whatsapp": "WhatsApp", "com.instagram.android": "Instagram", "com.google.android.gm": "Gmail",
    "com.google.android.apps.photos": "Photos", "com.android.vending": "Play Store", "com.google.android.dialer": "Phone",
    "com.google.android.apps.messaging": "Messages", "com.google.android.apps.youtube.music": "YT Music", "com.google.android.calendar": "Calendar",
    "com.google.android.gms": "Google services", "com.google.android.googlequicksearchbox": "Google app",
    "com.google.android.providers.media.module": "Photo picker", "com.android.settings": "Settings" };
  function appName(p) {
    if (!p) return "?";
    if (APPS[p]) return APPS[p];
    if (/\.exe$/i.test(p)) { var n = p.replace(/\.exe$/i, ""); return n.charAt(0).toUpperCase() + n.slice(1); }
    if (p.indexOf(".") < 0) return p;
    var seg = p.split(".").filter(function (s) { return ["com", "org", "android", "app", "apps", "google", "mobile", "net", "in", "io"].indexOf(s) < 0; });
    var last = seg.length ? seg[seg.length - 1] : p.split(".").pop();
    return last.charAt(0).toUpperCase() + last.slice(1);
  }
  var PALETTE = ["#8ab4f8", "#81c995", "#fdd663", "#f28b82", "#c58af9", "#78d9ec", "#ffa779", "#e6a8d7", "#a8c7a1", "#d2b48c", "#9aa0a6", "#7fb3c8", "#e0c36a", "#b39ddb", "#8fd3b6"];

  // ---------- sign-in and calls ----------
  function loadSession() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } }
  function saveSession(s) { try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); } catch (e) { /* private mode */ } }
  var session = loadSession();

  function authCall(path, body) {
    return fetch(CFG.url + path, {
      method: "POST", headers: { apikey: CFG.key, "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (r) { return r.text().then(function (t) { return { status: r.status, body: t ? JSON.parse(t) : {} }; }); });
  }
  function store(r, email) {
    session = { access: r.access_token, refresh: r.refresh_token, expires: Date.now() + (r.expires_in || 3600) * 1000,
      email: (r.user && r.user.email) || email };
    saveSession(session);
  }
  function token(force) {
    if (!session) return Promise.reject(new Error("signed-out"));
    if (!force && session.expires - Date.now() > 60000) return Promise.resolve(session.access);
    return authCall("/auth/v1/token?grant_type=refresh_token", { refresh_token: session.refresh }).then(function (r) {
      if (r.status >= 400) { session = null; saveSession(null); throw new Error("signed-out"); }
      store(r.body, session.email); return session.access;
    });
  }
  function rpc(name, args, retried) {
    return token(!!retried).then(function (t) {
      return fetch(CFG.url + "/rest/v1/rpc/" + name, {
        method: "POST", headers: { apikey: CFG.key, Authorization: "Bearer " + t, "Content-Type": "application/json" }, body: JSON.stringify(args || {})
      });
    }).then(function (r) {
      if (r.status === 401 && !retried) return rpc(name, args, true);
      if (r.status === 401) throw new Error("signed-out");
      return r.text().then(function (t) {
        var j = t ? JSON.parse(t) : null;
        if (!r.ok) throw new Error((j && (j.message || j.msg)) || ("Request failed (" + r.status + ")"));
        return j;
      });
    });
  }
  function dash(section, args) { return rpc("dashboard", { p_section: section, p_args: args || {} }); }

  // ---------- views ----------
  var VIEWS = [
    ["today", "Today", viewToday], ["schedule", "Schedule", viewSchedule], ["tasks", "Tasks", viewTasks], ["notes", "Notes", viewNotes],
    ["night", "Night job", viewNight], ["review", "Review", viewReview], ["patterns", "Patterns", viewPatterns],
    ["usage", "Usage", viewUsage], ["data", "Data", viewData]
  ];
  function empty(msg) { return h("div", { class: "empty", text: msg }); }
  function card(title, big, sub, extra) {
    return h("div", { class: "card" }, [h("h3", { text: title }), h("div", { class: "big", text: big }), sub ? h("div", { class: "muted small", text: sub }) : null, extra || null]);
  }
  function entryRow(e) {
    return h("div", { class: "row" }, [
      h("div", { class: "when", text: timeOf(e.start) + " – " + timeOf(e.end) }),
      h("div", { class: "grow" }, [h("span", { class: "dot " + (e.origin === "generated" ? "generated" : "") }), e.title,
        e.origin === "generated" ? h("span", { class: "tag", text: "generated" }) : null, e.edited ? h("span", { class: "tag warn", text: "edited" }) : null])
    ]);
  }

  function viewToday(root) {
    return dash("overview").then(function (o) {
      if (o.last_failure) {
        root.appendChild(h("div", { class: "banner" }, [h("strong", { text: "The night job failed: " + o.last_failure.step }),
          h("div", { class: "small", text: (o.last_failure.error || "No reason recorded") + " (" + stamp(o.last_failure.at) + ")" })]));
      }
      root.appendChild(h("div", { class: "grid" }, [
        card("Today's schedule", o.today_ready ? "Ready" : "Missing", dayLabel(o.today)),
        card("Tomorrow's schedule", o.tomorrow_ready ? "Ready" : "Not yet", dayLabel(addDays(o.today, 1))),
        card("Open tasks", String(o.tasks_pending), o.tasks_dropped + " dropped so far"),
        card("Notes", String(o.notes_total), o.notes_from_phone + " from the phone"),
        card("Phone today", dur(o.phone_seconds_today), "last upload " + ago(o.phone_last_upload)),
        card("Laptop", ago(o.laptop_last_seen), o.laptop_minutes_today + " min seen today")
      ]));
      root.appendChild(h("h2", { text: "Today" }));
      root.appendChild(o.schedule_today.length ? h("div", { class: "list" }, o.schedule_today.map(entryRow)) : empty("Nothing scheduled today."));
      root.appendChild(h("h2", { text: "Tomorrow" }));
      root.appendChild(o.schedule_tomorrow.length ? h("div", { class: "list" }, o.schedule_tomorrow.map(entryRow)) : empty("Nothing scheduled yet."));
      if (o.review_date) root.appendChild(h("p", { class: "muted small", text: "Latest review: " + o.review_date }));
    });
  }

  var weekStart = null;
  function viewSchedule(root) {
    if (!weekStart) weekStart = istToday();
    var to = addDays(weekStart, 6);
    return dash("schedule", { from: weekStart, to: to }).then(function (rows) {
      root.appendChild(h("div", { class: "toolbar" }, [
        h("button", { class: "btn ghost", text: "◀ Earlier", onclick: function () { weekStart = addDays(weekStart, -7); render(); } }),
        h("button", { class: "btn ghost", text: "This week", onclick: function () { weekStart = istToday(); render(); } }),
        h("button", { class: "btn ghost", text: "Later ▶", onclick: function () { weekStart = addDays(weekStart, 7); render(); } }),
        h("span", { class: "muted", text: dayLabel(weekStart) + " – " + dayLabel(to) })
      ]));
      var byDay = {};
      rows.forEach(function (e) { var d = parts(e.start).date; (byDay[d] = byDay[d] || []).push(e); });
      for (var i = 0; i < 7; i++) {
        var d = addDays(weekStart, i), list = byDay[d] || [];
        var track = h("div", { class: "track" }, list.map(function (e) {
          var a = parts(e.start), b = parts(e.end);
          var s = a.h * 60 + a.m, en = b.date === a.date ? b.h * 60 + b.m : 1440;
          return h("div", { class: "blk " + (e.origin === "generated" ? "generated" : ""), title: e.title,
            style: "left:" + (s / 14.4).toFixed(2) + "%;width:" + Math.max(0.6, (en - s) / 14.4).toFixed(2) + "%" });
        }));
        root.appendChild(h("div", { class: "day" }, [
          h("h3", { text: dayLabel(d) + (d === istToday() ? "  (today)" : "") }), track,
          h("div", { class: "ticks" }, ["0", "6", "12", "18", "24"].map(function (t) { return h("span", { text: t }); })),
          list.length ? h("div", { class: "list" }, list.map(entryRow)) : h("div", { class: "muted small", text: "No entries." })
        ]));
      }
      root.appendChild(h("p", { class: "muted small" }, [h("span", { class: "dot generated" }), "generated by the night job   ", h("span", { class: "dot" }), "yours"]));
    });
  }

  function viewTasks(root) {
    return dash("tasks").then(function (t) {
      root.appendChild(h("h2", { text: "Open (" + t.pending.length + ")" }));
      root.appendChild(t.pending.length ? h("div", { class: "list" }, t.pending.map(function (x) {
        var overdue = x.due && new Date(x.due) < new Date();
        return h("div", { class: "row" }, [h("div", { class: "when", text: x.due ? "due " + dayLabel(parts(x.due).date) : "no date" }),
          h("div", { class: "grow" }, [x.text, overdue ? h("span", { class: "tag bad", text: "overdue" }) : null, x.slips ? h("span", { class: "tag warn", text: "slipped " + x.slips + "×" }) : null])]);
      })) : empty("No open tasks."));
      root.appendChild(h("h2", { text: "Dropped (" + t.dropped.length + ")" }));
      root.appendChild(t.dropped.length ? h("div", { class: "list" }, t.dropped.map(function (x) {
        return h("div", { class: "row" }, [h("div", { class: "when", text: "dropped " + dayLabel(parts(x.dropped).date) }),
          h("div", { class: "grow" }, [x.text, h("div", { class: "muted small", text: x.reason || "stale" })])]);
      })) : empty("Nothing dropped."));
      root.appendChild(h("h2", { text: "Recently done" }));
      root.appendChild(t.completed.length ? h("div", { class: "list" }, t.completed.map(function (x) {
        return h("div", { class: "row" }, [h("div", { class: "when", text: x.completed ? dayLabel(parts(x.completed).date) : "" }), h("div", { class: "grow", text: x.text })]);
      })) : empty("Nothing completed yet."));
    });
  }

  var notesQuery = "", notesKind = "all", notesTimer = null;
  function viewNotes(root) {
    var list = h("div", { class: "list" });
    function load() {
      return rpc("search_notes", { p_query: notesQuery, p_limit: 100 }).then(function (rows) {
        clear(list);
        rows = rows.filter(function (r) { return notesKind === "all" || r.kind === notesKind; });
        if (!rows.length) { list.appendChild(empty(notesQuery ? "No matches." : "No notes yet.")); return; }
        rows.forEach(function (r) {
          list.appendChild(h("div", { class: "row" }, [h("div", { class: "when", text: stamp(r.created_at) }),
            h("div", { class: "grow" }, [r.text, r.kind === "journal" ? h("span", { class: "tag", text: "journal" }) : null])]));
        });
      }).catch(fail);
    }
    var box = h("input", { type: "search", placeholder: "Search words", value: notesQuery, "aria-label": "Search notes" });
    box.addEventListener("input", function () { notesQuery = box.value; clearTimeout(notesTimer); notesTimer = setTimeout(load, 300); });
    var chips = ["all", "note", "journal"].map(function (k) {
      return h("button", { class: "chip " + (notesKind === k ? "on" : ""), text: k === "all" ? "All" : k === "note" ? "Notes" : "Journal",
        onclick: function (ev) { notesKind = k; Array.prototype.forEach.call(ev.target.parentNode.children, function (c) { c.classList.remove("on"); }); ev.target.classList.add("on"); load(); } });
    });
    root.appendChild(h("div", { class: "toolbar" }, [box].concat(chips)));
    root.appendChild(list);
    return load();
  }

  function viewNight(root) {
    return Promise.all([dash("jobs"), dash("drafts")]).then(function (res) {
      var jobs = res[0], drafts = res[1];
      var groups = [], seen = {};
      jobs.forEach(function (j) {
        var k = j.kind + "|" + (j.day || "") + "|" + parts(j.at).date;
        if (!seen[k]) { seen[k] = { kind: j.kind, day: j.day, at: j.at, steps: [] }; groups.push(seen[k]); }
        seen[k].steps.push(j);
      });
      root.appendChild(h("h2", { text: "Recent runs" }));
      if (!groups.length) root.appendChild(empty("The night job has not run yet."));
      groups.slice(0, 14).forEach(function (g) {
        var bad = g.steps.some(function (s) { return !s.ok; });
        root.appendChild(h("div", { class: "card", style: "margin-bottom:10px" }, [
          h("div", {}, [h("strong", { text: g.kind }), " for " + (g.day ? dayLabel(g.day) : "?"), h("span", { class: "tag " + (bad ? "bad" : "ok"), text: bad ? "had failures" : "ok" }),
            h("span", { class: "muted small", text: "  " + stamp(g.at) })]),
          h("table", {}, [h("tbody", {}, g.steps.slice().reverse().map(function (s) {
            return h("tr", {}, [h("td", { text: s.step }), h("td", {}, [h("span", { class: "tag " + (s.ok ? "ok" : "bad"), text: s.ok ? (s.skipped ? "skipped" : "ok") : "failed" }),
              s.attempt > 1 ? h("span", { class: "tag", text: "attempt " + s.attempt }) : null]),
              h("td", { class: "small " + (s.ok ? "muted" : ""), text: s.error || s.skipped || "" })]);
          }))])
        ]));
      });
      root.appendChild(h("h2", { text: "Schedule drafts" }));
      root.appendChild(drafts.length ? h("div", { class: "list" }, drafts.slice(0, 15).map(function (d) {
        return h("div", { class: "row" }, [h("div", { class: "when", text: dayLabel(d.day) + " v" + d.version }),
          h("div", { class: "grow" }, [d.source + (d.model ? " · " + d.model : ""), h("span", { class: "tag", text: d.status }),
            h("div", { class: "muted small", text: d.proposed + " proposed, " + d.entries + " now" + (d.instruction ? " · “" + d.instruction + "”" : "") })])]);
      })) : empty("No drafts yet."));
    });
  }

  function viewReview(root) {
    return dash("review").then(function (v) {
      var r = v && v.data;
      if (!r) { root.appendChild(empty("No review yet.")); return; }
      var s = r.stats || {};
      root.appendChild(h("h2", { text: "Review for " + (r.date || v.date || "") }));
      root.appendChild(h("div", { class: "grid" }, [
        card("Planned", dur(s.planned_seconds)), card("At the laptop", dur(s.wall_clock_seconds)),
        card("Tasks done", String(s.tasks_completed || 0)), card("Notes", String(s.notes || 0))
      ]));
      var tl = r.timeline || [];
      root.appendChild(h("h2", { text: "Timeline" }));
      root.appendChild(tl.length ? h("div", { class: "list" }, tl.map(function (e) {
        return h("div", { class: "row" }, [h("div", { class: "when", text: timeOf(e.start) + " – " + timeOf(e.end) }),
          h("div", { class: "grow" }, [e.label || "", h("span", { class: "tag", text: e.kind })])]);
      })) : empty(r.quiet ? "A quiet day." : "Nothing on the timeline."));
      var tm = (r.tomorrow && r.tomorrow.schedule) || [];
      root.appendChild(h("h2", { text: "Tomorrow" }));
      root.appendChild(tm.length ? h("div", { class: "list" }, tm.map(function (e) {
        return h("div", { class: "row" }, [h("div", { class: "when", text: timeOf(e.start) + " – " + timeOf(e.end) }), h("div", { class: "grow", text: e.title })]);
      })) : empty("Nothing scheduled."));
      root.appendChild(h("p", { class: "muted small", text: "Reviews are built from data only and never show journal text." }));
    });
  }

  function viewPatterns(root) {
    return dash("patterns").then(function (rows) {
      if (!rows.length) { root.appendChild(empty("No patterns yet. They appear after the same thing has happened a few times.")); return; }
      var names = { active: "Active", questioned: "Maybe fading", candidate: "Candidates" };
      ["active", "questioned", "candidate"].forEach(function (st) {
        var sub = rows.filter(function (r) { return r.status === st; });
        if (!sub.length) return;
        root.appendChild(h("h2", { text: names[st] + " (" + sub.length + ")" }));
        root.appendChild(h("div", { class: "list" }, sub.map(function (r) {
          return h("div", { class: "row" }, [h("div", { class: "when", text: r.last_seen ? "last " + dayLabel(r.last_seen) : "" }),
            h("div", { class: "grow" }, [r.text, h("span", { class: "tag", text: r.kind }),
              h("div", { class: "muted small", text: r.occurrences + " times, " + r.misses + " misses" })])]);
        })));
      });
    });
  }

  var usageDays = 7;
  function stackedUsage(root, title, rows, from, to) {
    root.appendChild(h("h2", { text: title }));
    if (!rows.length) { root.appendChild(empty("No data in this period.")); return; }
    var total = {}, byDay = {};
    rows.forEach(function (r) { total[r.app] = (total[r.app] || 0) + r.seconds; (byDay[r.day] = byDay[r.day] || []).push(r); });
    var apps = Object.keys(total).sort(function (a, b) { return total[b] - total[a]; });
    var color = {}; apps.forEach(function (a, i) { color[a] = PALETTE[i % PALETTE.length]; });
    var max = 1; Object.keys(byDay).forEach(function (d) { max = Math.max(max, byDay[d].reduce(function (s, r) { return s + r.seconds; }, 0)); });
    for (var d = from; d <= to; d = addDays(d, 1)) {
      var list = byDay[d] || [], sum = list.reduce(function (s, r) { return s + r.seconds; }, 0);
      root.appendChild(h("div", { class: "usage-row" }, [h("div", { class: "small muted", text: dayLabel(d) }),
        h("div", { class: "stack", style: "width:" + Math.max(sum ? 2 : 0, sum / max * 100).toFixed(1) + "%" }, list.map(function (r) {
          return h("span", { title: appName(r.app) + " " + dur(r.seconds), style: "width:" + (r.seconds / sum * 100).toFixed(2) + "%;background:" + color[r.app] });
        })), h("div", { class: "small", text: sum ? dur(sum) : "–" })]));
    }
    root.appendChild(h("div", { class: "legend" }, apps.map(function (a) {
      return h("span", {}, [h("i", { style: "background:" + color[a] }), appName(a) + " " + dur(total[a])]);
    })));
  }
  function viewUsage(root) {
    root.appendChild(h("div", { class: "toolbar" }, [7, 14, 30].map(function (n) {
      return h("button", { class: "chip " + (usageDays === n ? "on" : ""), text: "Last " + n + " days", onclick: function () { usageDays = n; render(); } });
    })));
    return dash("usage", { days: usageDays }).then(function (u) {
      stackedUsage(root, "Phone", u.phone, u.from, u.to);
      stackedUsage(root, "Laptop", u.laptop, u.from, u.to);
      root.appendChild(h("p", { class: "muted small", text: "Phone shows app names only; banking apps are ignored on the phone. Phone and laptop time overlap, so do not add them up." }));
    });
  }

  var dataDays = 14;
  function viewData(root) {
    root.appendChild(h("div", { class: "toolbar" }, [7, 14, 30].map(function (n) {
      return h("button", { class: "chip " + (dataDays === n ? "on" : ""), text: "Last " + n + " days", onclick: function () { dataDays = n; render(); } });
    })));
    return dash("uptime", { days: dataDays }).then(function (u) {
      root.appendChild(h("h2", { text: "When the laptop was on" }));
      var cells = {}; var perDay = {};
      u.cells.forEach(function (c) { cells[c.day + "|" + c.hour] = c.beats; perDay[c.day] = (perDay[c.day] || 0) + c.beats; });
      var grid = h("div", { class: "heat" }, [h("div", {})].concat(Array.apply(null, Array(24)).map(function (_, i) { return h("div", { class: "hdr", text: i % 3 === 0 ? String(i) : "" }); })));
      for (var d = u.from; d <= u.to; d = addDays(d, 1)) {
        grid.appendChild(h("div", { text: dayLabel(d).replace(",", "").split(" ").slice(0, 2).join(" ") }));
        for (var hr = 0; hr < 24; hr++) {
          var b = cells[d + "|" + hr] || 0, f = Math.min(1, b / 60);
          grid.appendChild(h("div", { class: "cell", title: dayLabel(d) + " " + hr + ":00 – " + b + " min", style: f ? "background:rgba(138,180,248," + (0.15 + 0.85 * f).toFixed(2) + ")" : "" }));
        }
      }
      root.appendChild(grid);
      var days = Object.keys(perDay).length, total = Object.keys(perDay).reduce(function (s, k) { return s + perDay[k]; }, 0);
      root.appendChild(h("p", { class: "muted small", text: days ? "On about " + dur(total * 60 / days) + " on the days it was seen (" + days + " of " + dataDays + ")." : "No laptop activity in this period." }));
      root.appendChild(h("p", { class: "muted small", text: "Each cell is an hour (India time); darker means the laptop collector was running more of it. Gaps are why some days have thin data." }));
    });
  }

  // ---------- shell ----------
  var current = "today";
  var generation = 0;
  function fail(e) {
    if (e && e.message === "signed-out") { show(false); return; }
    var v = $("view");
    v.insertBefore(h("div", { class: "banner" }, [h("strong", { text: "Could not load this. " }), (e && e.message) || "Unknown error"]), v.firstChild);
  }
  function render() {
    var my = ++generation;
    var view = VIEWS.filter(function (v) { return v[0] === current; })[0] || VIEWS[0];
    Array.prototype.forEach.call($("nav").children, function (a) { a.classList.toggle("on", a.getAttribute("data-v") === view[0]); });
    var root = clear($("view"));
    root.appendChild(empty("Loading…"));
    var scratch = document.createElement("div");
    Promise.resolve(view[2](scratch)).then(function () {
      if (my !== generation) return;
      clear(root); while (scratch.firstChild) root.appendChild(scratch.firstChild);
    }).catch(function (e) { if (my === generation) { clear(root); fail(e); } });
  }
  function route() { var k = (location.hash || "#/today").replace("#/", ""); current = VIEWS.some(function (v) { return v[0] === k; }) ? k : "today"; render(); }
  function show(signedIn) {
    $("login").classList.toggle("hidden", signedIn);
    $("app").classList.toggle("hidden", !signedIn);
    if (signedIn) { $("who").textContent = session ? session.email : ""; route(); }
  }

  function init() {
    if (!CFG.url || !CFG.key) { document.body.textContent = "The dashboard is not configured (config.js)."; return; }
    var nav = $("nav");
    VIEWS.forEach(function (v) { nav.appendChild(h("a", { href: "#/" + v[0], "data-v": v[0], text: v[1] })); });
    window.addEventListener("hashchange", function () { if (session) route(); });
    $("refresh").addEventListener("click", render);
    $("logout").addEventListener("click", function () { session = null; saveSession(null); show(false); });
    $("login-form").addEventListener("submit", function (ev) {
      ev.preventDefault();
      var btn = $("login-btn"), err = $("login-error");
      btn.disabled = true; err.textContent = "";
      authCall("/auth/v1/token?grant_type=password", { email: $("email").value.trim(), password: $("password").value }).then(function (r) {
        if (r.status >= 400) { err.textContent = "Wrong email or password."; return; }
        store(r.body, $("email").value.trim()); $("password").value = ""; show(true);
      }).catch(function () { err.textContent = "Could not reach the server."; }).then(function () { btn.disabled = false; });
    });
    show(!!session);
  }
  init();
})();
