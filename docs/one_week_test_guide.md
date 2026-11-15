# One-week test guide

For the person who will **use Aside for seven days and judge it**. You do not need to read the code. You need a Windows
laptop, Chrome, and about 10 minutes a day. What matters most is whether the **schedule it drafts is good** and whether the
**context it builds is sensible and small**; the guide is built around those two things.

Everything you need to see is in the extension's Settings pages (open the panel, then the settings icon). A "tab" below
means a page in that Settings sidebar.

---

## 0. Before day 1 (30 minutes, once)

### 0.1 Set up
1. Get the project folder, and the `.env` file from the owner. It must contain `DATABASE_URL` and `GEMINI_API_KEY`
   (see `.env.example`). **Never use the owner's real database for experiments you want to throw away; ask which database
   you are on.**
2. In a terminal, in the project folder:
   ```
   .venv\Scripts\python.exe supervisor.py --status
   .venv\Scripts\python.exe supervisor.py
   ```
   This starts three things: the **collector** (records which app is in front), the **ingestor** (moves that into the
   database) and the **server** (port 8787). Logs are in `logs/`. To start it at every login: `supervisor.py --install`.
3. Chrome -> `chrome://extensions` -> Developer mode -> **Load unpacked** -> pick the `extension1` folder. Pin it.
4. Open the panel. If you see "Can't reach the local service", the server is not running; go back to step 2.

### 0.2 Check it works (all five must pass before you start)
| # | Do this | You should see |
|---|---|---|
| 1 | Panel, mode **Note**, type `zz first note`, Enter | It saves. Settings -> **Notes** shows it. |
| 2 | Mode **Task**, type `zz first task`, Enter | Settings -> **Tasks** shows it. |
| 3 | Mode **Chat**, type `zz say hi`, Enter | A reply appears. Settings -> **Context** shows a new entry for it. |
| 4 | Wait 3 minutes, open **Data gaps** | Collector and ingestor both show as running ("Ns ago"). |
| 5 | Settings -> **Tables** -> `events` | Fresh rows with your current app and the browser tab. |

Delete the `zz` rows afterwards (Notes and Tasks tabs).

### 0.3 Seed what it should know about you
Settings -> **Persistent file**. Add 6 to 10 short, true sentences, one per entry. These go into **every** prompt, so keep
them tight:
- `identity`: name, city, what you do.
- `routine`: when you wake, when you work, when you eat and sleep, weekday vs weekend.
- `preferences`: hard rules ("no meetings before 10:00", "gym is 45 min, never on Sunday").
- `goals`: what you are working toward this month.

Settings -> **Skills**: optionally add situational formats (starter ideas in `docs/skills.starter.json`).

### 0.4 Write down your baseline
Make a copy of the **results sheet** at the bottom of this file. Fill in today's date and write down your normal day in a
few lines. You will compare the drafts against this.

---

## 1. How the system works (read once, 5 minutes)

```
 YOU                        COLLECTED AUTOMATICALLY               BUILT BY CODE                  USED BY THE MODEL
 panel: notes, tasks,       laptop: app + window title (1/s)      sessions (apps used >=10 min)  persistent file
 schedule, chat,            Chrome: site + tab title (~1/min)     day records (plan vs actual)   skills, tasks, notes
 journal                    Google Calendar / Tasks sync          observations ("Patterns")      schedule, observations
                            phone: app names (not used yet)                                      candidates, last weekday
```

- **Chat** (Enter in Chat mode) = one Gemini call. It sees: persistent file, matching skills, today+tomorrow schedule,
  pending tasks, matching notes, up to 3 patterns, plus the **last 10 chat messages**.
- **Schedule draft** = one Gemini call. It sees: your instruction, persistent file, fixed blocks, the open draft, extracted
  facts ("candidates"), the same weekday last week as a template, tasks due, free time slots, patterns.
- **Nightly job** drafts *tomorrow* at about 20:45 IST and retries *today* at about 07:30 IST if nothing exists. It also
  extracts facts from your notes, finds patterns and writes the review.
- **Activity data (app and browser history) is not sent to the model.** It feeds sessions, the review and patterns only.
- Hard limits: **25 model calls per day** (resets at midnight Pacific = 12:30 IST in summer, 13:30 IST in winter), each context item cut at 500 tokens,
  chat context at most 8,000 tokens, schedule context at most 6,000.

Typing shortcuts in the panel: `note:` `journal:` `task:` `schedule:` `find:` (search notes, no model) `wrong:` (mark the
last reply wrong, optional reason). Start with `\` to skip prefix detection.

---

## 2. The daily routine (every day, 10 minutes)

### Morning (2 min)
1. Settings -> **Schedule**: is there a schedule for today? Did it come from the 07:30 retry or the previous night?
2. Settings -> **Drafts**: open today's draft. Read each block and its reason.
3. Write in the results sheet: did you follow it? Which blocks were wrong (time, order, missing, invented)?

### During the day (just live normally)
Use the panel **at least 5 times**: a task, a note, a schedule entry, one chat question, and one `find:`. Mix in:
- one task you then **postpone** (change its due date) - this feeds the "task slips" pattern;
- one note with a clear future fact, e.g. `note: travelling to Pune on Friday`;
- a change to the schedule (move or delete a block).

Leave monitoring on. If you pause it (Alt+Shift+M), write it down, because it creates a gap.

### Evening (5 min)
1. Settings -> **Review** -> **Preview today**: does planned vs actual match what really happened?
2. Settings -> **Data gaps**: is today's heatmap filled? Any "collector not running" gaps?
3. Settings -> **Context**: open the newest *chat* and *schedule* entries. For each, note the **token total** and look at
   what was **chosen** and what was **dropped** (and why).
4. Around 20:45 IST, the next day's draft appears (Drafts tab). Judge it before bed, then **Accept** it or **Discard** it.
   Do not edit it silently: if you change blocks, that is data too, so write down what you changed.

---

## 3. Day-by-day plan

| Day | Focus | Specific things to do |
|---|---|---|
| 1 | Baseline | Finish section 0. Use the panel normally. At night: accept or discard the draft, rate it 1-5. |
| 2 | Instructions | In the panel's Schedule tile, press **Draft tomorrow** (it only appears when tomorrow is empty) and give an instruction: "light day, finish by 6pm". Does the draft obey? Then revise it with "move lunch to 1pm". Locked (edited) blocks must stay. |
| 3 | Constraints from notes | Add `note: travelling Friday` and `task:` items due tomorrow. Next draft: are the tasks scheduled? Is the travel respected? |
| 4 | Chat memory | In Chat ask three connected questions ("what is on tomorrow?" -> "move the first one later" -> "why?"). Does it remember? Close and reopen the panel: is the thread back? After 10+ messages, does it still reply fast? |
| 5 | Failure handling | Watch for failed drafts (Connection log tab). Note how often the model says "high demand" (503) and whether the fallback model answered. Try a draft when the cap is nearly used. |
| 6 | Patterns & review | Open **Patterns**: are the observations true and useful? Delete wrong ones. Read the review for the week so far. |
| 7 | Verdict | Fill in the score table and the final questions (section 6). |

---

## 4. Where to look (the screens)

| You want to see | Go to |
|---|---|
| What was sent to Gemini, and what was left out | **Context** (every chat/schedule compile; tokens per source; replay) |
| The draft and why each block exists | **Drafts**, **Review** |
| Raw rows (events, sessions, notes, tasks, schedule...) | **Tables** |
| Is the collector running, and where are the gaps | **Data gaps** |
| What the code noticed about you | **Patterns** |
| What the assistant "knows" and how it changed | **Persistent file**, **What changed** |
| Model errors and connection problems | **Connection log** |
| Extracted facts ("tomorrow is a holiday") | **Statements** (inputs); candidates are read by the schedule draft |

**Token cost per call** is the "tok" total on each **Context** entry (what we packed) and `input_tokens` in the
`llm_trace` table (what Gemini counted). Expected sizes from earlier use: a schedule call is about 650-750 tokens in and
about 290 out; a chat call about 135 in. If you see anything above about 3,000 in, something is wrong; report it.

---

## 5. What to check (the actual tests)

### 5.1 Schedule quality (most important)
Score each generated draft 1-5 and tick:
- [ ] Nothing overlaps your fixed events (calendar items, meetings).
- [ ] Everything is inside waking hours (07:00-23:00).
- [ ] Tasks that were due are placed, **overdue ones first**, with their reason.
- [ ] Your routine (wake, meals, gym) is respected without inventing new habits.
- [ ] The instruction you gave is followed.
- [ ] Edited blocks you locked are untouched after a revision.
- [ ] Blocks are sensible lengths (not 5 minutes, not 4 hours).
- [ ] Nothing mentions private things that should not be in the plan.
- Count: blocks proposed / accepted as-is / edited / discarded. This ratio is the real quality number.

### 5.2 Context quality
In **Context**, for 3 schedule drafts and 3 chats:
- [ ] The facts it should have used were **chosen**, not dropped.
- [ ] Stale or wrong items did not get in (old tasks, a retired preference).
- [ ] Patterns shown are meaningful. **Known issue:** most current patterns say "You often record activity around HH:00",
  one for every hour; judge whether they help, and say so.
- [ ] Each source stays under its cap and the total stays small (see section 4).
- [ ] After using `wrong:`, the entry shows up with the compile it blames.

### 5.3 Chat
- [ ] Replies in under about 10 seconds typically.
- [ ] It remembers the last few messages, forgets older ones gracefully, and is not slower on day 7 than day 1.
- [ ] `find:` finds a note you wrote on day 1.
- [ ] `wrong:` is accepted and logged.

### 5.4 Data collection
- [ ] **Data gaps** shows no unexplained gaps (explained: paused, computer off).
- [ ] `events` has both `laptop` and `browser` rows every day.
- [ ] Sessions (Tables -> `sessions`) exist for your main apps and are not double counted.
- [ ] Review "actual" time is roughly right (within 20%).
- [ ] Restart the laptop once: the supervisor must come back by itself if installed.

### 5.5 Reliability and limits
- [ ] Record every failed model call and its message (Connection log).
- [ ] Number of calls used per day (a draft + chats + the nightly job). Never reaching 25 is the goal.
- [ ] If the cap is hit, you get a clear message, and nothing is sent.
- [ ] Kill the server mid-day and restart it: nothing typed in the panel is lost (tasks and notes are held and sent later).

---

## 6. Things you might hit (known, not your fault)

- **Gemini "high demand" (503) and timeouts** are common: about 1 in 3 schedule calls failed in earlier use. The system
  retries once on a second model. Report the rate, not each failure.
- **The Settings text** under "What may leave this machine" may say journal notes are never sent; the config currently
  **allows** them (3-month trial from 2026-10-08). Treat the config as the truth.
- **Phone usage data** is collected but not used yet.
- **Chat memory** is new (last 10 messages, each cut to 1,200 characters). The panel keeps 20 on screen.
- Window titles from the laptop collector are stored raw (they can include mail and chat titles). They are not sent to a
  model today. Do not browse anything you would not want stored for 90 days.
- Do **not** run `python -m evals.run --model ...`, `scripts/seed_*.py` or `python -m capture prune --apply` on the real
  database during this test.
- Do not touch the migration tooling (`alembic upgrade`). Take a `pg_dump` into `backups/` first and ask the owner.

---

## 7. If something is broken

| Symptom | Try |
|---|---|
| Panel says it can't reach the service | `supervisor.py --status`; if stopped run `supervisor.py`; read `logs/server.log` |
| No new `events` rows | `logs/collector.log` and `logs/ingestor.log`; Data gaps shows which is down |
| Chat says cap used | Wait for the provider day to reset (12:30-13:30 IST), or ask the owner to raise `[llm] daily_call_cap` |
| Draft button missing | Tomorrow already has entries, or a draft is open: discard it in Drafts |
| Nightly draft missing | Check the Connection log and the Actions tab of the repository; the 07:30 retry may still fill today |
| Extension looks stale after an update | `chrome://extensions` -> reload the extension, then restart the server |

Check the code is healthy: `.venv\Scripts\python.exe -m pytest -q` (about 2 minutes; it uses the test database, never
the real one, and never calls Gemini).

---

## 8. Results sheet (copy this, one per week)

**Tester:** ________  **Dates:** ________  **Database:** ________

| Day | Draft score (1-5) | Blocks proposed / kept / edited / dropped | Model calls used | Failed calls | Tokens in (schedule) | Notes |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |

**Final questions**
1. Would you follow the generated schedule without editing it? On how many of the 7 days?
2. Which kinds of mistakes repeated? (overlaps, ignoring a note, wrong lengths, inventing items...)
3. Which fact did you expect it to know that it never used?
4. Which context item showed up that it should not have?
5. Was chat fast and did it remember well enough?
6. Was anything collected that surprised you?
7. What did you stop doing because the tool was annoying?
8. One thing to fix first.
