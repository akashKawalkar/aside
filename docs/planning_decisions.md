# Planning and context design: decisions so far

Handoff for a new conversation. Everything below was decided with the user unless it is under "Open". The longer design, with formats, rules tables, the gold digest example and the reasoning flow, is in `docs/planning_design.md`. Read that next.

Nothing is built. No code was changed in either repo. No fake data was inserted: the DB had enough real data (events from Oct 6, generated calendar rows from Oct 9). Prototype scripts used for the data look lived in a session scratch folder and are gone; they only read the DB (read-only) and wrote local JSON.

## How to work with the user
- Short answers. Questions as numbered text lists, not a dialog tool.
- No over-engineering. Hands-on tasks (grants, device steps) are the user's.
- The user does not trust Gemini Flash (free tier, "not smart", often busy). Plan around that: code does all arithmetic and joins, the LLM only judges and writes, one worked example in every prompt, reasoning fields before answer fields.
- Free plan only. No stronger model.
- User runs their own server on port 8787. Test on 8788, tag and clean only your own DB rows. Real Gemini calls are allowed within the cap with synthetic prompts only.
- No Claude attribution lines in commits or PRs. Python repo: commit only when asked. Android repo (`C:/Users/akash/AndroidStudioProjects/aside`): commit as you go is allowed, do not push.

## Goal
Tomorrow's full-day schedule (written to Google Calendar) built from what actually happened, not from raw logs. Re-planning from user feedback is required. The LLM always outputs a plan and never asks questions. No template fallback.

## What goes into the LLM context
Decided: only practiced schedules (and things derived from them) reach the LLM.

- **Digital schedule** (plain code, built from laptop, phone and browser events). Never sent on its own.
- **Practiced schedule** = digital schedule + final Google Calendar + completed tasks, plus the generated-vs-final pair when they differ. Stored per day in `day_record`.
- **Digest**: one LLM call per day over that day's practiced schedule, cached. Fixed JSON, facts first, summary last. Numbers are attached by code. A hand-written gold example goes in the prompt (it is in `planning_design.md` section 2.4). Runs after 05:00 (the phone syncs usage about every 15 minutes while online).
- **Planning call inputs**: date and day; fixed events tomorrow; free slots (code); open tasks with due date and slip count; yesterday's full practiced schedule; digests for the 4 days before that (so 5 days total); patterns with evidence counts; real durations table (droppable); preferences and goals file (hand edited); yesterday's generated-vs-final diff if non-empty; journal entries from the last 2 days only; feedback text and current draft on a re-plan.
- **Cut**: raw usage, the digital schedule alone, general notes (as a context source), mood and energy, weather, review answers, older full schedules.
- Notes in general are excluded, but journal entries (stored as notes tagged `journal`) are included for the last 2 days.
- The `p:` notes written since the last plan are also seen by the planner (see phone section).

## Digital schedule rules (plain code)
- Two tracks: laptop and phone (they overlap in real time). Browser domain and title refine Chrome time on the laptop. Phone has app names only, no titles (accepted).
- 10-minute buckets, majority category per bucket, buckets with under 2 minutes of data skipped, adjacent same-category buckets merged. System apps ignored. Idle is its own block type.
- Each block keeps its top activity labels with minutes, **time ordered** (first appearance), e.g. `A 6m -> B 3m -> C 11m`.
- A `late-night` flag for blocks starting 23:00 to 06:00.
- Categories: coding, study, ai_assist, work_admin, comms, social, media, job_search, networking, wellbeing, shopping, other, system, idle. Seeded from an editable rules table (full seed in `planning_design.md` section 2.2). Notes: YouTube splits by title (shorts, learning counts as study, spiritual, other). LinkedIn Search and Jobs count as job_search, Feed and Post as social, profile and company pages as networking. These keyword splits need the user's review.
- Existing `sessions/sessionizer.py` is laptop only; do not reuse it.
- Calendar titles are tagged once by the LLM with expected categories (cached). Unmapped apps and sites get LLM-suggested categories that are auto-accepted, with a weekly review on Sundays (laptop).

## Practiced schedule rules
- Per final calendar block: on-plan minutes, idle minutes, phone minutes in the window, time-ordered activity lists for both tracks clipped to the window (detail minutes scaled to the clipped duration).
- Verdict: DONE at 60% or more on-plan, PARTIAL at 25% or more, else OFF-PLAN. `NO SIGNAL` for blocks the devices cannot observe (workout, meditation) with no evidence. Never counted as a miss.
- "Outside the plan": non-idle blocks of 30 minutes or more outside every calendar window, with top labels in time order.
- Day totals per track and category; tasks completed; open tasks with slip counts.
- Verdict thresholds approved by the user ("good").

## Patterns (v1 scope, from looking at 37 real days)
- Fixed 05:00-to-05:00 day. The user's sleep is irregular and they work after midnight, so midnight is wrong.
- Sleep: inferred from the longest stretch with no input on either device, then confirmed by the user on the phone (prefilled).
- Day-context label (normal, travel, festival, sick): from an all-day Google Calendar event, manual override allowed. Patterns exclude or down-weight non-normal days. Example: travel and a festival covered about Oct 26 to Nov 8.
- Data-health gate: a planned block with under 25% laptop data means the laptop was off; exclude it from adherence.
- v1 patterns, all computed by code, each shown as a claim with k/n days: adherence by block length; best and worst hours (on the 05:00 day); productive run-length distribution; phone co-use; recent trend (last 14 days vs prior 14); data-health flag; sleep window.
- Deferred: lagged effects (need 05:00 day, labelled days, about 4 weeks), experiments on the ideal/real dial and on breaks (randomize order), trigger detection (no signal at category level: 18 to 24% after every category).
- Findings (candidates, small n): adherence by length for laptop-on blocks: up to 90m 56% median (n=5), 91 to 150m 33% (n=5), over 150m 19% (n=22); 14:00 starts 11% median (n=13); productive share highest 23:00 to 02:00 and at 10:00 (midnight-based day, to be redone); productive run median 20m, p75 50m, p90 80m; phone social/media overlaps a median 29% of laptop-productive time (median 75m a day); weekly productive laptop minutes per day 417, 396, 24, 123, 180 (travel and festival weeks). The generator picked the 14:00 to 17:30 slots and the 3-hour blocks, so these findings describe the generator's choices, not the user's preference.
- Research notes (secondary sources only, figures not checked against primary papers): reference-class forecasting for durations; chronotype effects are debated; micro-breaks have small effects on vigor and fatigue and no significant effect on performance; habit formation averages 66 days with one miss not mattering; implementation intentions (if-then plans) have medium to large effects; JITAI literature suggests capping nudges (about 3 a day) and watching habituation; friction nudges look more promising than mindfulness prompts; small-sample personal data needs shrinkage and randomization; LLMs should read tool-computed statistics, not compute them. Related methods to consider in the second pass: eigenbehaviors (PCA over daily behavior vectors), Activity Rhythm Metric and diurnal-rhythm inference for sleep and regularity, time-constrained sequential pattern mining, PELT or Bayesian online change-point detection.
- Existing engine: `patterns/engine.py` and `patterns/stats.py` derive routines, focus hours, low scheduled overlap and task slips into the `observations` table. Keep the table and status lifecycle, replace the derivation to read practiced schedules, and replace the consecutive-miss logic with a windowed rate. "Focus hour" there counts any app activity with no categories.
- The user will tolerate dropping the estimate-accuracy module if it does not work, so keep it separate.

## Planning rules (preferences)
- Plan from actual data. Ideal-versus-real is one numeric dial passed in the prompt, tuned by experiment.
- Keep the user busy. No planned leisure. No risk flags. Every block has a short "why".
- Every block ends up fixed in Google Calendar, so no flexible or optional blocks.
- Hard work goes in the user's historically best hours.
- Workout and meditation are mandatory, at historical times. Meditation must be at least 4 hours after the last meal. Short meditation and short workout breaks do not count toward them.
- Meals: lunch 13:00 to 14:00 and dinner 20:00 to 21:00 by default, unless history or context supports another time. No meal tile on the phone.
- Planning grid: 30-minute slots, 48 per day, full 24 hours counted (no daylight constraint). The LLM assigns slot numbers; code converts to times and validates.
- Interleaving: blocks have a mode (study, build, physical, outdoor, rest, voice). At most 90 minutes of screen work in a row, then an off-screen break. Alternate screen modes. Meals, meditation and workout count as breaks. A break takes one slot and carries its own length (10 to 30 minutes); items can be combined in one break. Break menu, never invented: sing songs, short meditation, short workout, walk outside, household chores, speaking exercises.
- Caveat from the data: productive runs are median 20m and p90 80m, so the 90-minute cap rarely binds; block length of 30 to 50 minutes is realistic.

## Reasoning flow for the planning call
Step 0 slot grid (code, input). Step 1 anchors (sleep, meals, meditation, workout) with reasons, then free-slot count. Step 2 demand: list tasks and goal work, durations from real durations, rank, compare to free slots. Step 3 rate free windows strong, weak or risky with evidence counts. Step 4 placement using the rules above (high-slip tasks early, interleaving). Step 5 checklist of yes/no fields (every slot filled, meditation gap, due tasks placed, deep-work total matches the dial, yesterday's carryover, at least one goal served, screen run limit, mode alternation, at least one outdoor block), each "no" listing the fix. Step 6 final blocks with title, slots, mode, why. A re-plan adds a first step: restate feedback as constraints and keep untouched blocks. Start with one call; add a critic call only if experiments show it is needed. Details in `planning_design.md` section 5.

## Phone app (Android repo, separate)
- Pull, not push: the floating dot changes color; the user decides when to open it. No sound, no notification. At most about 3 distraction prompts a day plus the daily sheets.
- Colors (a ring around the dot, so a custom picture still works): neutral; amber after about 10 minutes on flagged apps within the last 30; red after about 30 minutes; blue when a question sheet is waiting. One color at a time, priority anomaly, daily sheets, distraction. The dot stays at full opacity while colored; one soft haptic at the threshold.
- Live detection: `CaptureService` polls `UsageStatsManager` about every 10 seconds while the screen is on (uses the existing Usage access; no accessibility service). Flagged apps seeded from the server rules: Instagram, YouTube (flagged by default; the question corrects it), Facebook. Editable in Settings.
- Distraction question (tap amber or red): shows "Plan now: <block>" and minutes on flagged apps, chips Break / Avoiding something / Actually useful / Other, optional voice or text for the planner. One tap saves and closes. Answers and taps (receptivity) upload as events and attach to the practiced schedule.
- Daily sheets (bunched, blue): morning sheet confirms the inferred sleep window (prefilled); evening sheet from 20:00 asks workout done, meditation done (Yes / Partly / No) and naps. Unanswered means unknown.
- Anomaly question only when laptop and phone are quiet for a long stretch with no calendar label: Travel / Sick / Festival / Normal.
- Prefixes in any text entry: `p:` instruction for the next plan only; `r:` standing rule (saved for review, then moved into the preferences file); existing `j:` journal, `t:` task, `n:` note. Works from the dot, Wispr Flow dictation, the extension and the sheet text box. The Settings page explains the prefixes.
- Existing, no build needed: usage collection and upload (WorkManager, about every 15 minutes), capture of notes, journal and tasks, the night-job watchdog and failure notification, Wispr Flow as a normal input method.
- New server work: a read-only call returning today's plan blocks to the phone (the existing phone calls are capture, delete, search, usage upload, `schedule_ready`, `latest_failure`); a table or event kind for bubble answers, sheet answers and receptivity taps.
- Existing relevant code: `overlay/DotController.kt`, `overlay/CaptureService.kt`, `overlay/EditorController.kt`, `usage/UsageCollector.kt`, `cloud/Sync.kt`, `capture/Prefix.kt`.

## Rejected
Mood and energy logging, notebook and photo capture (tasks live in a pocket notebook and the user does not want to change that), a "just ate" tile, a stronger model, daylight constraints for outdoor breaks, flexible or optional blocks, risk flags, planned leisure, notes as a general context source.

## Open
1. **Pattern detection second pass.** Current evidence is thin (5 to 22 blocks per finding, 37 days with two disrupted weeks). Time-in-category is not output, and tasks and journal data are empty or junk (the tasks table is mostly test rows; there are no journal notes yet). Hour-of-day adherence is confounded by the generator's slot choices, so learning real best hours needs deliberate variation. Category rules are still seeds (ai_assist counts as productive even for unrelated chats; LinkedIn is a guess). No replay harness exists to test whether patterns improve plans. The pattern lifecycle (confidence, decay, drop) needs redoing and detection methods need to be chosen.
2. **The planning prompt itself**: system prompt, schema, worked example, the ideal/real dial's default value.
3. **Guardrails** for validating Flash output (no overlaps, inside the grid, fixed events untouched, retry on violation). The user said "we will work on it".
4. Flash reliability: retry, fallback and rate limits.
5. Server pieces for the phone (plan-fetch call, answers table) and the Android build.
6. Other LLM outputs (daily and weekly review, nudges), later.
7. Parked: whether propose-versus-place (LLM proposes, code places) is wanted; what makes a plan "good" (followed more versus edited less).
8. A generated-vs-final comparison needs the real field names in `schedule_draft.proposed` and `entries`; a naive JSON comparison gave a false "not identical".

## Suggested next step
Write the planning prompt (system prompt, output schema, worked example), since the data it consumes is now defined. Then the build order in `planning_design.md` section 8.
