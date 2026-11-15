# Planning design: context, practiced schedule, digest, planning prompt

Status: design only. Nothing here is built. Prototype scripts for the digital and practiced schedule were run read-only against real data (Nov 12, 2026) and are not in the repo.

## 1. Goal

Produce tomorrow's full-day schedule (written to Google Calendar) from what actually happened, not from raw logs.
Flash (free tier) is the only model, so code does all arithmetic and joining, and the LLM only judges and writes.

## 2. Data pipeline

```
events (laptop, browser, phone) --code--> digital schedule
digital schedule + Google Calendar (final) + tasks --code--> practiced schedule (per day)
practiced schedule --1 LLM call/day--> digest (cached)
patterns (code, from practiced history)
        \
digests + yesterday + patterns + tasks + fixed events + prefs + journal --> planning call
```

Only practiced schedules (and things derived from them) reach the LLM. Raw usage, the digital schedule alone, and the calendar alone are never sent separately.

### 2.1 Digital schedule (plain code)

1. Take the day's events from `events` (source laptop/browser/android), local time Asia/Kolkata.
2. Laptop: idle events cut app time. A Chrome fragment takes its domain and title from the overlapping browser event (majority by overlap). Phone has only the package name (no titles available). That is accepted.
3. Each fragment gets a category and an activity label from an editable rules table (section 2.2). `system` apps (shell, f.lux, explorer, HWiNFO and similar) are ignored.
4. Bucket into 10-minute buckets. Each bucket takes its majority category. Buckets with under 2 minutes of data are skipped. Adjacent buckets with the same category merge into one block.
5. Laptop and phone are two separate tracks, because they overlap in real time.
6. Each block keeps its top labels with minutes, in time order (first appearance), e.g. `A 6m -> B 3m -> C 11m`.
7. A block starting 23:00 to 06:00 gets a `late-night` flag.

Findings from the prototype:
- Merging raw fragments directly gives about 250 jittery blocks (tab switching). Buckets give about 30.
- Existing `sessions/sessionizer.py` is laptop-only (app allowlist, 10 minute minimum). Do not reuse it. Build separately.

### 2.2 Category rules (seed, editable, overridable)

| Source | Category | Note |
|---|---|---|
| Code.exe | coding | label `VS Code: <project> (<tab>)` |
| Acrobat, libgen, phone PDF reader | study | label `PDF: <title>` |
| ChatGPT (web, phone) | ai_assist | label with chat title |
| YouTube | media, or study if title matches learning keywords | split by title: shorts, learning, spiritual, other. Late-night flag by time |
| Instagram, Facebook | social | stories vs feed in label |
| LinkedIn Search, Jobs | job_search | ambiguous: could be lead-finding (see open items) |
| LinkedIn Feed, Post, Notifications | social | |
| LinkedIn profile/company pages | networking | |
| Job-portal domains (naukri, indeed, wellfound, internshala, glassdoor, foundit) | job_search | |
| Gmail, dialer, truecaller | comms | |
| Notion | work_admin | |
| Isha Foundation app | wellbeing | |
| Myntra | shopping | |
| f.lux, shell, explorer, HWiNFO, etc. | system | ignored |
| anything else | other | list top unmapped apps/domains daily for the user to map |

Keyword lists (learning, spiritual) are seeds and must be user-editable.

### 2.3 Practiced schedule (plain code)

Per day. For each final calendar block (`schedule` rows, `deleted_at is null`):
- Calendar titles are tagged once by the LLM with expected categories (cached by title). Prototype used hand-written tags.
- Compute on-plan minutes (digital time in expected categories), idle minutes, phone minutes in the window, and the time-ordered activity list for both tracks, clipped to the window. Detail minutes are scaled to the clipped duration.
- Verdict: `DONE` at 60% or more on-plan, `PARTIAL` at 25% or more, else `OFF-PLAN`. Blocks whose expected category the devices cannot observe (exercise, wellbeing) with no signal get `NO SIGNAL`. That is not a miss.
- Add "outside the plan": non-idle digital blocks of 30 minutes or more that fall outside every calendar window, with their top labels in time order.
- Add day totals per track and category.
- Add tasks completed that day (`completed_at`) and open tasks with `slip_count`.
- Add the generated-vs-final pair (`schedule_draft.proposed` vs `entries`) only if they differ. The prototype compared the JSON naively and got a false "not identical"; the real diff needs the real field names.
- Stored in `day_record` (per-day JSON).

Format sketch (Nov 12, abridged):

```
10:00-13:00  AI Engineering Study Block  [generated, unedited]  -> OFF-PLAN
   planned 180m | on-plan laptop 40m (22%) | idle 20m | phone in window 90m
   laptop 10:50-11:10 study  Libgen: browsing books 14m
   laptop 11:50-12:10 media  YouTube: Chaewon... 1m -> YouTube: home/feed 1m -> YouTube (spiritual): ... 3m -> ... 6m
   phone  10:10-11:10 media  YouTube app 37m
OUTSIDE THE PLAN (>=30m, non-idle)
   laptop 13:20-14:30 70m media
          YouTube: home/feed 6m -> YouTube shorts: GTA radio... 6m -> ... 11m
```

### 2.4 Digest (one LLM call per day, cached)

- Runs once per day after the phone usage upload (upload timing not yet checked; see open items). Result is cached. Regenerate only if the day's calendar is edited afterwards.
- Input: that day's full practiced schedule, time-ordered.
- Output: fixed JSON, facts first, summary last: `plan_blocks` (verdict, on_plan_pct, note), `time_sinks` (top 3), `best_stretch`, `anomalies`, `summary`.
- Numbers (totals, percentages, minutes) are attached by code. The LLM only writes the interpretation.
- Prompt contains one worked example (Nov 12 gold example below). The user does not trust Flash, so the example is mandatory.
- Also returns `title_tags` (suggested categories for unmapped titles), which feed the rules cache.

Gold example (Nov 12):

```json
{
  "facts": {
    "plan_blocks": [
      {"title": "Morning Workout", "verdict": "NO SIGNAL"},
      {"title": "Daily Meditation Routine", "verdict": "NO SIGNAL",
       "note": "phone shows YouTube 08:40-09:00 inside the window"},
      {"title": "AI Engineering Study Block", "verdict": "OFF-PLAN", "on_plan_pct": 22,
       "note": "40m of Libgen browsing and a PDF; the rest was YouTube, Instagram, one unrelated ChatGPT chat and idle"},
      {"title": "AI Engineering Hands-on Practice", "verdict": "PARTIAL", "on_plan_pct": 28,
       "note": "~20m ChatGPT on VLLM architecture in 3 bursts with idle gaps; LinkedIn Search 38m; phone Instagram 165m throughout"}
    ],
    "time_sinks": [
      {"what": "Phone Instagram", "minutes": 350, "note": "174m during the 14:30 practice block"},
      {"what": "Phone YouTube", "minutes": 370, "note": "120m after midnight, 80m at 22:00"},
      {"what": "LinkedIn Search (laptop)", "minutes": 160, "note": "unclear whether job search or lead-finding"}
    ],
    "best_stretch": "23:20-00:00 coding on linkedin-lead-finder (34m), outside the plan",
    "anomalies": [
      "Both planned study blocks were accepted unedited and mostly not followed",
      "Late-night: phone YouTube 00:00-02:00, then 08:40 YouTube again"
    ]
  },
  "summary": "The plan was accepted unedited and followed only in fragments. Real study happened in short bursts (Libgen, an LLM-serving PDF, VLLM chats), while Instagram and YouTube filled most of the day. The most focused work was 40 minutes of coding at 23:20, unplanned."
}
```

Rules the example teaches: state verdicts and numbers code already computed; never claim what the data cannot show (workout); say so when the data is ambiguous.

### 2.5 Patterns and real durations

- Patterns come from practiced history, not raw data. Each is a short structured claim with evidence counts shown to the LLM (e.g. "best focus 15:00-17:00, 4/5 days").
- Real durations (planned vs actual per block type) are a separate, droppable module that only reads practiced rows. Deleting it loses nothing else. Coarse, category-level matching first.

### 2.6 Pattern detection (v1 scope, from a look at real data)

Looked at 37 days (Oct 9 to Nov 14, 2026) of real events rebuilt through the digital-schedule rules, plus 53 generated work blocks. Scripts were scratch only, not in the repo. No synthetic data was needed.

**Day definition**
- Fixed 05:00-to-05:00 day. Midnight is wrong: the user often works after midnight, which splits a night across dates and made a lag test (late-night media vs next-day output) come out backwards. That result was discarded.
- Sleep is inferred separately as the longest stretch with no input on either device. Sleep start, end and length become a pattern of their own. Not yet tested as a predictor of adherence. The phone's morning sheet asks the user to confirm the inferred sleep window and the evening sheet asks about naps (section 6.4).
- Day-context label (`normal`, `travel`, `festival`, `sick`) comes from an all-day Google Calendar event, with a manual override. Patterns weigh `normal` days fully and exclude or down-weight the rest. The user was travelling and then at a festival from about Oct 26 to Nov 8, which explains the collapse below.

**Data-health gate**
- A planned block with under 25% laptop data in its window means the laptop was off. It is excluded from adherence, not counted as a miss. 18 of 53 work blocks were like this.
- Workout and meditation blocks (69 of about 125 planned) have no device signal, so they never count as a miss.

**Findings (all candidates, with counts shown; see caveats)**

| Pattern | Result | Planner use |
|---|---|---|
| Adherence by block length (laptop-on blocks) | <=90m 56% median (n=5); 91-150m 33% (n=5); >150m 19% (n=22) | Prefer 30-90 minute blocks. 39 of 53 generated blocks were over 150m |
| Adherence by planned start hour | 14:00 starts 11% median (n=13); 10:00 33% (n=6); 12:00-13:00 61-83% (n=1-2 each) | Avoid putting hard work at 14:00. The generator chose those slots, not the user, so this describes the generator's choices |
| Productive share by clock hour | Highest 23:00-02:00 (40-47%) and 10:00 (47%); lowest 20:00-21:00 (18-22%). Computed on the midnight day, to be redone on the 05:00 day | Place hard work in strong hours |
| Productive run length | Median 20m, p75 50m, p90 80m, 21% of runs 60m or more, max 250m. Partly an artifact of 10-minute buckets flipping category | Realistic block length is 30-50 minutes. The 90 minute cap rarely binds |
| Phone co-use | Phone social/media overlapped a median of 29% of laptop-productive time (median 75m per day) | The phone is the competitor. Feeds nudge design |
| Weekly trend | Productive laptop minutes per day by week: 417, 396, 24, 123, 180. Phone social/media per day: 176, 293, 526, 463, 563 | Planner sees the last 14 days against the prior 14, not an all-time average. Disrupted weeks are labelled, not learned from |
| Trigger before laptop distraction | 18-24% after every category | No signal at this resolution. Do not claim a trigger |

Caveats:
- The short-block adherence figure rests on n=5.
- Hour-of-day numbers are from the midnight-based day.
- Tasks are mostly test rows and there are no journal rows. Neither can feed patterns until real input exists.

**v1 pattern set (computed by code)**
1. Adherence by block length.
2. Best and worst hours, on the 05:00 day, with counts.
3. Productive run-length distribution.
4. Phone co-use.
5. Recent trend, last 14 days against the prior 14.
6. Data-health flag.
7. Sleep window (inferred).

Deferred: lagged effects (need the 05:00 day, labelled days and about 4 weeks), experiments on the ideal/real dial and breaks (randomized order), trigger detection.

**Open design work:** the pattern-detection method needs a second pass. Methods from related fields (routine discovery, change-point detection, sequential pattern mining, digital phenotyping) are to be reviewed before settling the final approach.

## 3. Planning call inputs

Target: a full-day schedule for tomorrow only. Re-plan from feedback is supported.

| # | Piece | Format | Why |
|---|---|---|---|
| 1 | Date, weekday, 24-hour day | 1 line | Anchor |
| 2 | Fixed events tomorrow (Google Calendar, non-generated) | `HH:MM-HH:MM title` | Hard constraints |
| 3 | Free slots | list of slot ranges | Code does the arithmetic |
| 4 | Open tasks | title, due date, slip count, source | Due date plus slips is the priority signal. Sources: Google Tasks, phone, extension |
| 5 | Yesterday's practiced schedule, in full | section 2.3 format | Continuity |
| 6 | Digests for the 4 days before yesterday | digest JSON, trimmed | Recent trend |
| 7 | Patterns with evidence counts | claim + count | Best hours, adherence |
| 8 | Real durations | table (droppable) | Padding from actual data |
| 9 | Preferences and goals | hand-edited file | Meals, meditation gap, workout, goals, break menu, mix dial |
| 10 | Yesterday's generated-vs-final diff | only if non-empty | Rejection signal |
| 11 | Journal entries, last 2 days only | text | Reasons and intentions the devices cannot show |
| 12 | Feedback (re-plan only) | current draft plus feedback text | Iteration |

Excluded: raw usage, the digital schedule alone, general notes, mood and energy, weather, review answers, older full schedules.

## 4. Planning rules (preferences file content)

- Plan from actual data. Ideal-vs-real is a single numeric dial passed in the prompt (e.g. `ideal_weight: 0.3`), tuned by experiment.
- Keep the user busy. No planned leisure. No risk flags. Every block has a short "why" line.
- Every block ends up fixed in Google Calendar, so there are no flexible or optional blocks.
- Hard work goes in the user's historically best hours.
- Workout and meditation are mandatory, at their historical times unless data says otherwise.
- Meditation must be at least 4 hours after the last meal.
- Meals: lunch 13:00-14:00, dinner 20:00-21:00 by default. Use a different time if history or context supports it.
- The short meditation and short workout breaks do not count toward the mandatory ones.
- The LLM never asks questions. It always outputs a plan. No template fallback.

### Interleaving and breaks

Each block has a mode: `study` (screen, taking in), `build` (screen, producing), `physical`, `outdoor`, `rest`, `voice`.
- At most 90 minutes (3 slots) of screen work in a row, then an off-screen break.
- Alternate screen modes (study then build, not the same twice in a row).
- Meals, meditation and workout count as breaks. Use them before adding new ones.
- No daylight constraint: the full 24 hours counts, so outdoor items may go at any hour.
- Breaks are active, not leisure.
- Break menu (picked from, never invented): sing songs, short meditation, short workout, walk outside, household chores, speaking exercises. Lengths are roughly 10 to 30 minutes. Items may be combined in one break (e.g. a walk plus a breathing exercise, or a chore).
- Planning grid stays 30-minute slots. A break occupies one slot and carries its own length; the remainder is a transition buffer.

## 5. Reasoning flow for the planning call

The LLM works in steps and writes each step into its output (JSON fields ordered so facts come before the plan). It assigns slot numbers, never clock times. Code converts to times and validates.

0. **Slot grid** (code, given as input): 48 slots, fixed events already filled in.
1. **Anchors**: sleep (wake and sleep slots from preferences and yesterday), meals (defaults or history), meditation (4h after last meal, historical time), workout. For each: slots plus a one-line reason. Then the count of free slots left.
2. **Demand**: list everything that wants time (tasks due or slipped, goal work). Each gets a duration in slots from the real-durations table, and a ranked reason. Compare to free slots: if over, state what gets cut; if under, fill with goal work.
3. **Rate the free windows**: strong, weak or risky from patterns and digests, with evidence counts (e.g. "14:30-17:30 risky: phone Instagram 3 of 5 days").
4. **Placement**: hard work in strong windows, shallow work in weak ones, nothing important in risky ones. Block length from actual sustained focus, scaled by the ideal dial. High-slip tasks early in the day. Apply the interleaving rules from section 4.
5. **Checklist**, each a yes/no field, any "no" lists the fix made:
   - every free slot filled
   - meditation gap respected
   - all tasks due tomorrow placed
   - deep-work total matches the dial
   - yesterday's carryover accounted for (late night, unfinished block)
   - at least one long-term goal served
   - no screen run over 3 slots
   - no same mode back to back
   - at least one outdoor block
6. **Final plan**: blocks with title, slots, mode, why. Code validates and writes to Google Calendar.

Re-plan from feedback adds a step before step 1: restate the feedback as constraints, then keep every block the feedback does not touch.

If one-call plans prove weak, a second call as critic (review the plan against the checklist) can be added. Start with one call and add the critic only if experiments show it is needed. Flash needs: reasoning fields before answer fields, one worked example in the prompt, and narrow questions.

## 6. Capture: phone bubble and user input

Android app: separate repo `C:/Users/akash/AndroidStudioProjects/aside`. Existing pieces used: `overlay/DotController` (floating dot), `overlay/CaptureService` (foreground service), `overlay/EditorController` (input window), `usage/UsageCollector` (reads usage events), `cloud/Sync` (WorkManager job, about every 15 minutes while online, collects and uploads usage), `capture/Prefix` (`j:`, `t:`, `n:` prefixes).

### 6.1 Principle
Pull, not push. The bubble changes color and the user decides when to open it. No sound, no notification. At most about 3 distraction prompts a day, plus the daily sheets below.

### 6.2 Bubble colors (ring around the dot, so a custom picture still works)
- Neutral: no ring.
- Amber: about 10 minutes on flagged apps within the last 30. Red: about 30. The dot stays at full opacity while colored (it normally fades to 40%). One soft haptic at the threshold.
- Blue: a question sheet is waiting (daily sheets, anomaly).
- One color at a time. Priority: anomaly, then daily sheets, then distraction.

Live detection: while the screen is on, `CaptureService` polls `UsageStatsManager` every ~10 seconds for the foreground app (uses the Usage access already granted; no accessibility service). Flagged apps, seeded from the server rules: Instagram, YouTube (flagged by default even though some use is useful; the question corrects it), Facebook. The list is editable in Settings.

### 6.3 Distraction question (tap an amber or red bubble)

```
Plan now: AI Study Block -> 17:30      (or "No plan block right now")
Instagram + YouTube: 32 min
[ Break ] [ Avoiding something ] [ Actually useful ] [ Other ]
say or type anything for the planner...   (mic)
```

One tap on a chip saves the reason and closes the sheet. The text box is optional. Answers upload as events and attach to the practiced schedule as the reason for that block. Tapping versus ignoring is logged as a receptivity signal by hour and app. Reset the color after an answer.

### 6.4 Daily sheets (bunched, blue bubble)
- **Morning sheet** (first activity after waking): sleep confirmation, prefilled from inference: "Looks like you slept 03:10 to 10:40. Right?" One tap to confirm, or edit bedtime and wake time.
- **Evening sheet** (from 20:00): workout done? meditation done? (Yes / Partly / No), plus "Any naps today?" (None, or add start and length). Unanswered stays "unknown", never "missed".
- **Anomaly question**, only when the system detects one: laptop and phone quiet for a long stretch with no all-day calendar label. Options: Travel / Sick / Festival / Normal. Asked once.
- **Weekly category review, Sundays.** Auto-accepted LLM category suggestions for unmapped apps and sites are listed on the laptop (extension settings), with a reject option.

### 6.5 Instructions for the planner (prefixes in any text entry)
- `p: ...` instruction for the next plan only (e.g. "no calls after 6", "go lighter").
- `r: ...` standing rule. Saved for review, then moved into the preferences file.
- Existing: `j:` journal, `t:` task, `n:` note.
- Works from the dot, Wispr Flow dictation, the extension, and the sheet's text box. The planner sees `p:` notes written since the last plan.
- The app's Settings page lists what each prefix does.

### 6.6 What each piece of input feeds

| Info | Source | Effort |
|---|---|---|
| Distraction reason | Amber/red bubble, 1 tap | optional |
| Workout, meditation, naps | Evening sheet | 1 to 3 taps |
| Sleep window | Morning sheet, prefilled | 1 tap |
| Day context | Calendar all-day event (silent), else anomaly question | 0 normally |
| Planner instructions | `p:` / `r:` | on demand |
| Tasks | Google Tasks, `t:`, extension (exist) | 0 new |
| Journal | `j:` (exists), voice | 0 new |
| Category corrections | Sunday review list | about 1 min a week |
| Plan edits | Google Calendar, automatic | 0 |
| Completed tasks | automatic | 0 |
| Meal times | Defaults only (13:00-14:00, 20:00-21:00); no meal tile | 0 |

### 6.7 New server work
- A read-only call that returns today's plan blocks to the phone (the existing phone calls do not include a schedule fetch).
- A table or event kind for bubble answers, sheet answers and receptivity taps, uploaded like usage.
- The digest and practiced schedule read these answers as labels.

### 6.8 Not doing
Mood and energy logging, notebook or photo capture, a "just ate" tile. Wispr Flow dictation works as a plain input method and needs no build.

## 7. Open items

- Phone usage upload timing: resolved. The WorkManager job runs about every 15 minutes while online, so a digest after 05:00 is safe.
- LinkedIn Search: job search or lead-finding? The title alone does not say, and the VS Code project `linkedin-lead-finder` suggests some is work.
- The YouTube and LinkedIn splits use keyword seeds that need the user's review.
- Numeric ideal/real dial: default value and tuning plan.
- Patterns: exact set, and whether code or LLM extracts them.
- Guardrails (code validation and retry on LLM output): to be worked out.
- Propose-vs-place (LLM proposes, code places): parked.
- Success measure for a plan (followed more vs edited less): parked.
- Other LLM outputs (daily and weekly review, nudges): later.
- Flash reliability (retry, fallback, rate limits): later.
- Calendar-title tagger: a cached LLM call, build with the rules table.

## 8. Build order (proposed, nothing started)

1. Rules table plus digital schedule builder.
2. Practiced schedule builder, `day_record` storage.
3. Digest call with the gold example.
4. Patterns and real durations.
5. Planning prompt, and re-plan from feedback.
