# Aside: AI Engineer interview prep (dense)

Framing: the ideal case. Aside shipped, I use it every day, the cloud edition runs every night, and the evals have been run.
**Rule for myself:** every number written as `[__]` is a slot for a real measurement. Fill it in before the interview or leave it out.
Every other number in this file comes from the repo, the traces or `docs/gemini_api_findings.md`.

---------------------------------------------------------------------------------------------------------------------
## 0. Pitches

**10 seconds.** Aside is a personal assistant that stays out of the way. It captures notes, tasks and my schedule, records what
I actually do on my laptop and phone, and each night uses an LLM to plan my next day into Google Calendar. All of it runs
on a free-tier model budget of 25 calls a day.

**60 seconds.** Aside is a full-stack AI system I built alone: a Python/FastAPI backend on Postgres + pgvector (Supabase), a
Chrome side-panel extension, an Android capture app, and a nightly GitHub Actions job. The AI engineering is in five places:
(1) a **context compiler** that packs persistent memory, skills, schedule, tasks and notes into a token budget, with a logged
reason for every item it drops, and lets me replay any compile under a different recipe; (2) a **single gated LLM client**
that enforces a daily call cap, a privacy filter and a trace on every attempt (OpenTelemetry GenAI shaped); (3) **schedule
generation** where the LLM proposes time blocks and code validates them, with a rule-based baseline and fallback;
(4) **nightly extraction** that turns journal entries into typed, dated, confidence-scored candidate facts; (5) a
**code-only pattern engine** whose observations have a lifecycle. I measured the real Gemini API before designing the call
path: hidden thinking tokens, load-driven latency of 2.6 s to 92 s, and 503s that last for minutes. That is why the system
uses per-model reasoning effort, a 45 s per-attempt timeout and a single fallback model.

**What makes it different from a "RAG chatbot" project:** the hard parts are context management, a constrained model
budget, data quality over time, and honest measurement. It is not a prompt wrapper.

---------------------------------------------------------------------------------------------------------------------
## 1. System map (draw this on the whiteboard)

```
 Chrome side panel ─┐                         ┌─> Gemini (OpenAI-compatible endpoint)
 Android app (RPCs)─┼─> FastAPI /input router ─┤        ▲ only through ApprovedClient
 Desktop collector ─┘     │                    │        │ (grant + quota + privacy + trace)
                          ▼                    │  context compiler (sources → items → packer → render)
                  Postgres + pgvector  <───────┘
                  (Supabase, 22+ tables, RLS, security-definer RPCs)
                          ▲
 GitHub Action (20:45 IST nightly, 07:30 morning retry):
   calendar_pull → sessionize → day_record → drop_stale → extract → patterns → generate → calendar_push → review
                          ▲                                                        │
                 Google Calendar / Tasks  <────────────────────────────────────────┘ (advisory lock vs laptop sync)
```

| Layer | Tech | Purpose |
|---|---|---|
| Backend | Python 3.12, FastAPI, psycopg3 async pool, Alembic (~25 migrations) | capture, routes, workers |
| Storage | Postgres + pgvector (HNSW), Supabase in prod, local Postgres for tests | single source of truth |
| Embeddings | `all-MiniLM-L6-v2`, 384-dim, local, loaded lazily | note search, no data leaves for embeddings |
| LLM | Gemini 3.x Flash (OpenAI-wire adapter), fallback `gemini-3.1-flash-lite` | chat, schedule, extraction |
| UI | Chrome MV3 side panel + settings (vanilla ES modules, Material 3 dark) | capture, drafts, inspection |
| Mobile | Kotlin, Compose, Views overlay, Room, WorkManager, UsageStatsManager, BiometricPrompt, Keystore | capture, usage, watchdog |
| Cloud | GitHub Actions cron, Supabase Auth/RLS/RPCs, GitHub Pages site + read-only dashboard | nightly brain, no server to run |
| Quality | pytest (626+ tests), FakeClient, CDP-driven headless-Chrome UI harness, CI workflow | verification |

**Core modules:** `capture/` (server, router, collector), `context/` (items, recipe, packer, gate, privacy, sources),
`llm/` (client, approved, resilient, quota, trace, tokens, fake, adapters), `schedule_gen/`, `extractor/`, `patterns/`,
`review/`, `sessions/`, `knowledge/`, `storage/`, `gcal/`, `cloud/`, `evals/`.

---------------------------------------------------------------------------------------------------------------------
## 2. Context engineering (the core; expect the most questions here)

**Q: How do you build the prompt?**
Four concepts. **Source** returns Items. **Item** carries text, source, group, tokens, priority, provenance, confidence,
valid_from/valid_to and status. **Recipe** is a TOML config per situation (chat / schedule / extraction) that lists the
sources with a priority, a `cap_fraction`, and optional `max_items` and `min_confidence`. **Packer** is greedy: sources go in
recipe-priority order, items in item-priority order. Each item is either chosen or dropped with a reason: `not_in_recipe`,
`filtered:<status>`, `low_confidence`, `expired`, `not_yet_valid`, `max_items`, `source_cap` or `over_budget`. If an item
does not fit, the packer moves on, so a smaller item after it can still fit. **Render** owns all formatting
(`## source → ### group → bullets`); a source returns plain text and never markdown.

**Q: Priority order and why?**
Chat: current_session 95 > persistent_file 90 > skills 80 > schedule 70 > tasks 60 > notes 50 > observations 40 >
history 30. Stable identity and hard constraints beat retrieved material. The schedule outranks notes because the schedule
is a commitment and a note is loose memory. Schedule recipe: instructions (95) outrank even the persistent file, because
they are the user's request for this exact draft.

**Q: How is the budget set?**
`min(window × input_fraction, max_input_tokens)`. **Bug story:** with Gemini's 1M window, a 0.5 fraction gave a 524,288-token
"budget" that never bound, so the packer, the per-source caps and the drop reasons were all inert. Absolute ceilings
(chat 8000, schedule 6000, extraction 4000) brought them back. A real chat compile now packs about 324 tokens against
8000. The ceiling also guards the free-tier tokens-per-minute allowance.

**Q: Why not just stuff the 1M context window?**
Cost, latency, rate limits (TPM), lost-in-the-middle degradation, and auditability. A small prompt I can read is a
prompt I can debug. Every compile is logged (offered / chosen / dropped / tokens / recipe hash) and shown in the
**Context viewer** ("why did it say that").

**Q: Replay?**
`POST /context/compiles/{id}/replay?recipe=` re-packs the stored inputs under another recipe. The `wrong:` gesture logs a
mark against the compile behind the last call that was actually sent, so each mark becomes a regression case. I can
replay those cases locally with no model call (`evals/replay_wrong.py`), because the stored compiles hold personal text.

**Q: Token counting without the provider tokenizer?**
I estimate `chars / chars_per_token × 1.15`. I measured Gemini at 3.38 chars/token on structured prompts (times, markdown,
JSON) and ~4.0 on prose. The old 4.0 estimate undercounted a real schedule prompt (525 estimated vs 531 actual), so
Gemini profiles use 3.5. The estimate errs high on purpose.

**Q: Selector?**
A separate, removable stage: `off | rules | llm`. Rules use situation tags. The LLM selector sees only descriptors,
picks only among optional items, and falls back to rules. It is off by default because it costs one extra call, and I
evaluate it against rules by replaying stored compiles.

**Q: Injection / untrusted content?**
A result gate (`context/gate.py`) applies to every context item and tool/DB result: size caps (`max_item_tokens = 500`),
truncation with a pointer note, dropped unused columns, read-only SQL with a forced LIMIT (`guard_sql`), and tool output
treated as data. The model never has write authority: chat never writes to the schedule, and a schedule draft is
only a proposal until the user (or the validated nightly path) accepts it.

**Q: Memory design (the "persistent file")?**
Five sections (identity/stable facts, hard preferences and constraints, weekday/weekend routine skeleton, goals/focus,
people). Each entry has text, source (user/agent/note/pattern), created, last_confirmed, evidence_count and status.
**Conflict rule:** a new fact is stored as provisional next to the old one. The old one is retired once the new one has
been seen on 3 separate days with no contradiction. Retired facts stay in the log and are hidden from context. Every
write goes into `diff_log` and can be undone (`POST /persistent-file/undo/{diff_id}`). Size cap 1500 tokens; eviction
order is least evidence / longest unused, with goals and people evicted first. **Temporal validity** is a property of
each item, so expired facts drop out by themselves.

**Q: Skills?**
A skill is a format, a procedure or a decision tree; anything that fits in one sentence belongs in the persistent file.
Skills are loaded descriptor-first: name, `use_when` (triggers matched as whole words) and `affects`. At most 2 per call,
about 600 tokens each. Usage and correction counts are tracked, and never-used or often-corrected skills are flagged.
Only the user edits skills. I chose rule-based loading over embedding the skills because it is deterministic and
easy to debug at this scale.

---------------------------------------------------------------------------------------------------------------------
## 3. Retrieval (RAG part)

**Q: How does note search work?**
It is hybrid. **Keyword arm:** case-insensitive, any word of 3+ letters for context (the search box requires all words),
with `%`/`_` escaped. **Semantic arm:** local MiniLM embeddings in pgvector, cosine similarity with a cutoff. Results are
merged. Embedding runs asynchronously: notes are written with `embedding_status = pending` and a worker embeds them every
5 s, so capture is never blocked by the model.

**Q: Biggest retrieval bug?**
Notes could not be reached from chat. On a real query, the relevant notes scored 0.477 and 0.428 and the irrelevant
ones ≤ 0.27, but the inherited cutoff of 0.65 dropped everything. The keyword arm also matched the whole query as one
substring, so it never matched a natural question. Fix: a configurable cutoff (0.40), an any-word keyword arm, and a
labelled query→must/must-not set to calibrate the cutoff from the score distribution instead of guessing.
Calibrated result: precision@k `[__]`, recall `[__]`.

**Q: Why local embeddings?**
Privacy (embedding a note sends nothing out), zero quota, and enough quality for short personal notes. 384 dimensions
keeps the HNSW index small. The trade-off: MiniLM is weaker on paraphrase, which the keyword arm and the LLM's own
reading partly cover.

**Q: HNSW + filters?**
Filtered vector search on HNSW can under-return results, because the filter is applied after the approximate search
finds candidates. At my data size an exact scan is cheap, so the fix is to raise `ef_search` or use exact search below
a row threshold. I verified this with `[__]`.

**Q: Reranking? Chunking?**
Notes are short, so one note is one vector and there is no chunking. A cross-encoder reranker is the obvious next step if
the corpus grows. At current size it would add latency and dependencies for little gain.

**Q: Phone search?**
A database function (`search_notes`) does any-word matching across all notes, including notes written on the laptop. The
phone merges in its own unsent items. There is no semantic search on the phone, because the embedder is not there.

---------------------------------------------------------------------------------------------------------------------
## 4. The LLM gateway: one door to the model

**Q: How do you stop runaway spend / quota burn?**
`llm/factory.create_client` returns an `ApprovedClient` that wraps the raw adapter. The raw adapter is private. A call
needs a **Grant** (a `contextvar` set by `with approved(Grant(tier, operation, compile_log_id))`). Without one it raises
`ApprovalRequired`. Background-tier grants only work when `background_enabled` is on. Before every attempt the client
counts today's `llm_trace` rows against `daily_call_cap` (25) and raises `QuotaExceeded` without making the call.
**Every attempt is traced, success or failure**, because a failed call still uses allowance and the counter has to stay
honest. The design point: a convention ("remember to check quota") is exactly what the next feature forgets, so the gate
is structural.

**Q: Why count your own calls?**
The API exposes no header for remaining quota, so the local counter is the only instrument. The counter follows the
**provider's day (midnight Pacific)**, not IST, because the quota resets on Pacific time.

**Q: Two tiers?**
*User-initiated* calls (chat, "Draft tomorrow", revise) go out at once inside a user grant. *Background* calls (extraction,
the nightly schedule, pattern naming) never prompt, run inside the quota, are auditable afterwards, and were blocked until
the privacy layer existed. I originally had an approval card on every call ("read the exact prompt, then approve"). I
removed it once the cap, the privacy layer and the traces were in place, because approval fatigue turns a card into a
rubber stamp.

**Q: Provider abstraction?**
A thin `LLMClient` protocol (`complete(messages, …) -> Completion`) plus a `ModelProfile` from `config/models.toml`
(window, costs, chars/token, reasoning_effort). Swapping models changes config only. Adapters: OpenAI-compatible (Gemini)
and `FakeClient` (all development and tests). There is no LangChain or LangGraph in the core (see §12).

---------------------------------------------------------------------------------------------------------------------
## 5. Reliability: what the real API does (measured, not assumed)

I studied the API with 18 probes through the same gate (`scripts/api_probe.py`, results in JSONL, re-runnable):

| Finding | Evidence | Design response |
|---|---|---|
| Gemini 3.x thinks by default, cannot turn it off, and hides the thinking tokens | "Reply OK": 1 visible / 104 hidden; schedule prompt: 83 visible / **817 hidden** at default effort, 0 at `low`, same quality | `reasoning_effort` per model profile (`low`); `thinking_tokens = total - prompt - completion` in every trace |
| Thinking counts inside `max_tokens` | cap 50 → empty answer, `finish=length` | `max_tokens` 3000/2000; `finish_reason == length` = incomplete, surfaced as `truncated` |
| Supported effort values differ by model | 3.7-flash + `minimal` → HTTP 400 | effort lives in the profile with its evidence; a test forbids the bad pair |
| Latency is driven by server load | n=14: median ~5 s, p90 ~31 s, max **92 s** (that call had 0 thinking tokens) | 45 s per-attempt timeout; two attempts fit inside the UI's 130 s |
| 503 "high demand" lasts for minutes on one model | 3.8-flash failed 2 of 2, 20 min apart; lite answered in ~3 s twice | `FallbackClient`: **one different model** on 5xx/timeout/429; never retry the same model; never retry 400/401 |
| JSON mode | no `response_format` → fenced JSON; `json_object`/`json_schema` → bare | JSON mode + a fence-tolerant parser + a validator |

**Live validation:** the primary model returned 503 after 6.1 s, the call fell back to lite, and a valid 2-block draft
arrived 3.1 s later (~9 s total instead of a failure). Both attempts were traced.

**Q: Why not exponential backoff?**
Because the measurement showed that overload on one model persists for minutes. Backing off on the same model wastes the
user's wait and the quota. Switching models works. Backoff fits rate limits on a healthy service, not capacity outages.

**Q: Why not streaming?**
The stalls happen before the first byte, so streaming would not fix the slow cases. For structured output you need the
whole object anyway. Perceived latency is handled in the UI (an elapsed-seconds counter and a "why it's slow" note after 15 s).

**Q: Temperature?**
0.3 for schedules (structure, determinism) and 0.7 for chat.

**Q: Graceful degradation?**
A feature must never depend on one model answering. Chain: primary → fallback model → **rule-based placeholder draft**
(copy the last same weekday, fill free slots) → the morning retry run → the phone watchdog re-dispatches the workflow →
a quiet failure notice only after the retry also failed.

---------------------------------------------------------------------------------------------------------------------
## 6. Schedule generation (LLM proposes, code decides)

**Pipeline:**
1. **Gather** (code): fixed user entries for tomorrow, tasks due (overdue and postponed tasks offered, stale ones dropped
   first), the last same-weekday snapshot, the top-3 observations, instructions scoped to tomorrow, candidate items valid
   tomorrow, persistent-file constraints, and a free-slot map.
2. **Draft** (LLM, JSON mode): meaningful blocks, plus time blocks for the tasks that should happen that day. A block can
   carry a `task_id`. Each block has a stored reason.
3. **Validate** (code): overlaps with fixed entries (`overlaps_fixed`), waking hours, the 30-min default, IST, and
   rejected proposals kept with their reason.
4. **Store** as a versioned `schedule_draft`. `proposed` is immutable (what the model said). `entries` is the working copy.
5. User accepts per entry or all at once; accepted entries are tagged `generated`. Revising with a follow-up instruction
   regenerates with the untouched and accepted entries **hard-locked** (carried over verbatim; the model is never asked
   to return them).
6. The nightly path pushes the result to Google Calendar.

**Q: Why keep `proposed` immutable?**
The learning signal is **first draft vs final** (the last state of the day). Edits in between go to `schedule_log` for
audit and undo only, and must not feed review, patterns or draft-vs-final. If they did, the metric would be noisy and
could be gamed.

**Q: How do you evaluate schedule quality?**
(a) Validator pass rate (blocks proposed vs kept). (b) Edit distance between first draft and final (blocks kept as-is,
moved, deleted, added). (c) Adherence: time actually spent in planned blocks, from laptop/phone sessions.
(d) Comparison against the rule-based baseline on the same days. Ideal-case results: acceptance `[__]%`, blocks kept
unedited `[__]%`, LLM vs baseline adherence `[__]` vs `[__]`.

**Q: Why not let the LLM write directly to the calendar?**
Write authority stays with code and the user. The validator is the safety net: the lite model once proposed a duplicate
of a fixed block, and the validator rejected it, which is exactly what the validator is for.

---------------------------------------------------------------------------------------------------------------------
## 7. Extraction (unstructured to structured, nightly)

**Q: What does it do?**
One batched background call per night over the day's statements (notes, journal, schedule instructions; raw text kept
immutable). It produces **candidate items** typed `actual | plan | prediction | constraint | preference`, each with
text, effect, valid_from/valid_until, confidence, source_id and reason. A journal entry weighs more than a plain note.
Doubtful items are shelved: kept, but not used. Items expire with their dates. They can be viewed and deleted in settings,
and the extraction can be re-run with a better model.

**Q: Why batched and not per note?**
Quota: 1 call instead of N. It also gives better cross-statement reasoning, for example "tired" plus "skip gym tomorrow"
together become one constraint.

**Q: Why run it before schedule generation?**
So that a journal entry typed on the phone at 20:45 shapes tomorrow's plan in the same nightly run.

**Q: Quality / hallucination control?**
A conservative system prompt ("if unsure, don't extract"), a strict JSON schema, type and date validation, and a
confidence threshold in the recipe filter (`min_confidence`). Every item carries its source_id, so it is attributable.
Items are provisional and can only affect a draft, never write a fact directly. Precision on a hand-labelled week:
`[__]`.

---------------------------------------------------------------------------------------------------------------------
## 8. Pattern engine (deliberately not an LLM)

Code computes statistics: weekday vs weekend routines, the focus-hour distribution, planned vs actual (sessions overlapping
schedule blocks), task slip counts, and low-"intentional"-activity days. **Intentional** = time that overlaps a schedule
entry or relates to a task; the rest is "unplanned", never "bad". **Slip** = due date moved later repeatedly, expired, or
still pending past due.
**Lifecycle:** candidate → active after 3 occurrences → shown as a question after 2 consecutive misses → dropped after 3.
Runs at 13:00 and 21:00 IST. Top 3 are injected into context. The LLM may at most *name or explain* a pattern, once a day.

**Q: Why code-only detection?**
It is deterministic, free, testable and auditable. LLMs are bad at counting across 90 days of rows, and a pattern that
claims a fact must be reproducible. Observations, never facts.

**Data asset (the long-term value):** the schedule edit log + origin flag, nightly snapshots, the task due-change log (the
slip signal), the monitoring pause log + collector heartbeat (so a gap has a reason), a nightly `day_record`, and a
data-gaps heatmap in settings. Without gap reasons, "you did nothing on Tuesday" and "the collector was off" look the same.

---------------------------------------------------------------------------------------------------------------------
## 9. Evals

**Q: How do you evaluate an assistant like this?**
- **Constraint adherence:** a code-checkable constraint ("under 50 words", "no emojis") is seeded at turn 1 and checked at
  turns 5/10/15/20, **with and without pinning** it into the persistent file. Results `[__]`. Expected shape: adherence
  decays without pinning and stays flat with it.
- **Cross-session carry-over:** a preference stated in session 1 must be applied unprompted in session 2.
- **Standard question set:** 15 questions with criteria and a human "feel" rating that comes first. I don't use an LLM
  judge for feel; criteria are prompts for reflection, not automatic scores.
- **Regression replay:** every `wrong:` mark is a stored compile I can replay under new recipes.
- **Schedule:** the metrics in §6 against the rule-based baseline.
- **Retrieval:** the labelled query→must/must-not set in §3.

**Q: How do you eval on a free tier?**
Develop the whole harness against `FakeClient` (so it tests the plumbing, not quality), then spend real calls on 2–3
synthetic scenarios. The runner refuses a run unless the provider-day budget covers it **with 5 calls of headroom**,
disables the fallback so the call count is bounded, and uses only synthetic inputs, because free-tier prompts may be used
by the provider. A constraint scenario costs 20 calls; cross-session costs 2.

**Q: LLM-as-judge?**
Only with code-checkable constraints wherever possible. For subjective quality I trust my own review on a small set over a
judge model with its own biases. If I scaled up, I would calibrate a judge against my labels first (agreement / κ) before
trusting it.

---------------------------------------------------------------------------------------------------------------------
## 10. Observability

Every attempt is one span in `llm_trace`, shaped after the **OpenTelemetry GenAI semantic conventions**: trace/span/parent
ids, provider, request/response model, input/output tokens, hidden thinking tokens, finish_reason, error, latency, cost,
operation label, `compile_log_id` (links the call to the exact context that built it), and the truncated flag.
`to_otel()` maps a span to the standard so I can export to Langfuse or any OTel backend without reshaping. A test pins the
mapping, because the GenAI conventions are still "Development" and attributes have been renamed (e.g.
`gen_ai.system` → `gen_ai.provider.name`). Prompt content is opt-in, never the default. Nightly steps each write a
`job_run` row with the failure reason.

**Q: Why your own table, not Langfuse?**
Personal data stays in my database. The trace table also *is* the quota counter. An exporter interface allows Langfuse
for synthetic eval runs only.

---------------------------------------------------------------------------------------------------------------------
## 11. Privacy and security

- **Privacy layer** (`context/privacy.py`): one function, `denied_reason`, used both by the context compiler and by the
  extractor. Config: `deny_sources` and `deny_tags`; `journal` was denied by default (now allowed under an explicit
  3-month opt-in). It is a hard prerequisite for any background call. It is shown read-only in settings.
- Embeddings are local. Sensitive domains lose their titles at capture. The review shows no domains or titles and ignores
  the journal. Banking apps are ignored on the phone.
- **Supabase:** RLS on every table with no anon policies. The phone calls `security definer` RPCs only (`capture_item`,
  `delete_item`, `search_notes`, `upload_usage`, `schedule_ready`, `latest_failure`), granted to `authenticated` only,
  with `anon`/`public` revoked. Sign-ups are off. The phone never learns the table layout.
- **Android:** biometric/PIN lock on reading, while capture via the dot stays open ("anyone can add, only I can read").
  Tokens are sealed with an Android Keystore key. Backup and device transfer are off, because auto-backup would have
  copied the journal to Drive. The app-switcher preview is blank. Notifications never contain note text. The GitHub token
  is fine-grained (Actions on one repo).
- Secrets live only in `.env` / Actions secrets. The repo holds code only.
- Read-only SQL with a forced LIMIT for any tool-style DB access.

**Q: Prompt injection via a note?**
Notes are data in a bulleted, rendered context section. The model has no tools that write or delete. All outputs go
through a parser and validator, and a schedule cannot overwrite fixed entries. The worst case is a bad draft, which the
validator and the user catch.

---------------------------------------------------------------------------------------------------------------------
## 12. Design decisions and trade-offs (the "why")

| Decision | Why | Cost accepted |
|---|---|---|
| No LangGraph/LangChain in core | the pipeline is linear and deterministic; frameworks hide prompts and add churn; plain Python is easy to test with FakeClient | write my own small abstractions (kept tiny); A3 plan: a LangGraph runner behind an interface for comparison |
| MCP only at the edges | internal tools are plain Python with a descriptor-first interface that can wrap an MCP server | none yet |
| LLM proposes, code validates | correctness and write-safety | must maintain a validator |
| Review and patterns are code-only | deterministic, free, no silent background calls | less "smart" prose |
| Greedy packer, not optimal knapsack | priorities matter more than squeezing the last token; explainable drop reasons | slightly suboptimal fill |
| One fallback model, no retry loop | measured failure mode is model-specific overload | one extra model to characterise |
| Serverless nightly (GitHub Actions) | free, no always-on server, laptop can be off | cron jitter (delays seen); hence morning retry + phone watchdog |
| Idempotent nightly steps (`ONCE` set) | a rerun never makes a second model call for the same day | state in `job_run` |
| Advisory lock for Calendar sync | laptop and Action must never sync at the same time | Postgres-specific |
| Postgres for everything (vectors too) | one store, transactions, joins between vectors and metadata | not a dedicated vector DB |
| Synthetic seed with manifest teardown | dev data without polluting the real asset; exact row-count restore verified | seed/teardown discipline |

**Q: Is it an "agent"?**
Honest answer: it is a set of LLM workflows with a single gated client, not an autonomous tool-calling loop. I removed an
early LangGraph-style agent because a deterministic pipeline was more reliable and cheaper for these tasks. An agent loop
pays off when the steps are unknown in advance. Here they are known. A chat agent with tools is planned with draft-first
writes and no deletes unless told.

---------------------------------------------------------------------------------------------------------------------
## 13. Cloud edition and integrations

- **Nightly job** (`cloud/nightly.py`, `python -m cloud nightly|morning_retry|manual`): ordered steps; a failing step is
  recorded and the run continues (the schedule does not wait for the review). The schedule step retries on model errors,
  but not on quota or permission errors. Cron at 20:45 IST (tomorrow) and 07:30 IST (today, a no-op if a draft exists).
  `concurrency` group prevents overlapping runs. A slim `requirements-action.txt`, and a test that the nightly packages
  import with torch/psutil/fastapi blocked (the lazy embedder keeps the Action fast).
- **Google Calendar/Tasks two-way sync:** OAuth refresh token, client protocols with a fake for tests, Google wins
  conflicts, title + time only, links cleared if the calendar id changes, the laptop syncs at start-up / every 5 min /
  shortly after edits.
- **Stale-task rule:** more than 4 days overdue or postponed twice → `dropped` with a reason (visible in task history),
  instead of silently deleting.
- **Phone watchdog:** if tomorrow has no schedule by 21:10–22:30 IST, the phone dispatches the workflow once.
- **Website:** GitHub Pages landing, privacy and terms pages, plus a read-only dashboard backed by one DB function.

---------------------------------------------------------------------------------------------------------------------
## 14. Android app (if asked about mobile / edge)

The phone is a sensor and a capture tool, not a brain. A floating dot (foreground service, `TYPE_APPLICATION_OVERLAY`)
opens an input window with the keyboard up. Text is autosaved on every keystroke into Room and committed when the window
is dismissed, so nothing is lost on a crash. Chips and prefixes (`j:`, `t:`) set the type. A WorkManager job (~15 min,
plus an expedited run after each commit) does four things: an outbox sync with idempotent client ids, usage sessions
from `UsageStatsManager` (package names only), the watchdog, and the failure notice. Pure, unit-tested cores:
`SessionBuilder`, `WatchdogPlan`, prefix parsing, commit rules. Voice uses the on-device `SpeechRecognizer`.

---------------------------------------------------------------------------------------------------------------------
## 15. Testing and engineering practice

- 626+ pytest tests: pure unit tests plus DB integration tests against a separate test database (they skip if the URL is
  not local; only `zz`-tagged rows or rows dated 2001 are used, and they clean up after themselves).
- **FakeClient for every LLM path.** A real call is a deliberate, counted act and never part of a test run.
- **Real-browser UI harness** (`scripts/ui_check`): copies the extension, runs the real app on a test port with a fake
  model and a fixed 2031 clock, drives headless Chrome over the DevTools protocol (no Node), asserts, screenshots, then
  deletes everything it created. It found 5 bugs, each of which broke a whole page, that static tests had missed. A static
  wiring test (imports resolve, icons exist, template backticks intact) now catches 4 of those 5 classes.
- Migrations only after `pg_dump` into `backups/`. A test runs every repo function against the real table so model/DB
  drift cannot recur (it caught the skills schema mismatch).
- Generated code maps (`llmmap.txt`, `codemap.json`) for fast orientation, including by AI coding tools.
- CI workflow on GitHub Actions.

---------------------------------------------------------------------------------------------------------------------
## 16. War stories (STAR-ready)

1. **The budget that never bound.** *S:* Prompts looked fine, but the drop log was always empty. *T:* Find out why the
   context limits did nothing. *A:* Traced it to `window × fraction` = 524k for a 1M-window model; added absolute ceilings
   in the recipe hash; added a test that a seeded compile produces drops. *R:* The packer, the caps and the explainability
   all came back on; the chat prompt went from unbounded to ~324 / 8000 tokens.
2. **The first real call timed out.** *A:* Instead of raising the timeout, I ran a 22-call measured study of the API
   (thinking, latency, 503s, quota reset, JSON modes) and wrote the findings up with caveats ("a trend, not a proof").
   *R:* Per-model effort, a fallback, a 45 s timeout, provider-day quota; a 503 became a 9 s success.
3. **Empty answers with `finish=length`.** Hidden thinking ate a 1000-token cap (77 visible, ~920 hidden). Fix: larger
   caps, `truncated` treated as an error state and shown to the user.
4. **Notes unreachable.** A 0.65 cutoff vs real scores of 0.43–0.48 → labelled-set calibration (§3).
5. **Pages that never worked.** The Skills and Statements tabs checked `res.ok` on an envelope that only has `status`, so
   they always said "Failed to load". Lesson: look at the real UI. This led to the CDP harness plus a scenario that opens
   every tab.
6. **Fictional data in real prompts.** The synthetic seed's facts were leaking into real calls. I tore it down using the
   row manifest and verified that the counts were restored exactly (915→933→915). Lesson: dev data and the real asset
   must be separable by construction.
7. **A NULL type inference bug** (`CASE WHEN %s IS NULL` — Postgres could not infer the parameter type) crashed every
   draft edit. The DB integration tests caught it before any user did.

---------------------------------------------------------------------------------------------------------------------
## 17. Scaling and "what next" questions

**Q: 10,000 users?**
Multi-tenant: a `user_id` on every row plus RLS policies per user (RPCs already run as the signed-in user). Move the
nightly job from Actions to a queue plus workers (one job per user, sharded by time zone). Paid tier with per-user
token budgets instead of a global call cap; the gate already models quota per call. Batch API for nightly extraction
(cheaper, latency-insensitive). Prompt caching for the static persistent-file prefix. An embedding service, and pgvector
partitioning or a dedicated vector index. Trace export to OTel. LLM-judge evals calibrated against human labels, run in CI
on synthetic personas.

**Q: Cost per user per day?**
About 1 extraction + 1 schedule + N chat calls. With low effort, a ~600-token schedule prompt plus ~100 visible output
tokens costs fractions of a cent at Flash prices (input $0.75/M, output $3.75/M for 3.8 Flash; doubling on 2027-01-01, as
recorded in `models.toml`). Measured mean per day: `[__]`.

**Q: What would you improve?**
A calibrated retrieval cutoff with a reranker; a learned or LLM selector evaluated by replay; LLM naming of patterns;
merging phone and laptop time (union of spans, per-device breakdown); citation-based context usage tracking (the reply
returns the ids of the items it used, to prune sources that are never used); a fine-tuned small model for extraction once
labelled data exists; streaming chat; a LangGraph runner comparison (A3).

**Q: Fine-tuning vs prompting?**
Prompting plus context engineering first; there is not enough labelled data yet. Extraction is the best candidate for
fine-tuning or distillation later: a narrow schema, a clear label set, repeatable nightly volume, and the shelved and
confirmed items become free labels.

---------------------------------------------------------------------------------------------------------------------
## 18. General AI-engineering questions, answered through the project

- **RAG vs long context:** long context is available (1M) but budgeted; retrieval plus priority packing beats stuffing
  on cost, latency and debuggability.
- **Hallucination:** structured output + schema + code validator + provenance on every item + conservative extraction +
  the user as final authority.
- **Structured output:** `json_object`/`json_schema` mode, a fence-tolerant parser, a validator, rejected items kept with
  reasons.
- **Latency:** low reasoning effort, a small prompt, a per-attempt timeout, a fallback model, UI feedback; background work
  moved to the nightly job.
- **Cost control:** the call cap at a single chokepoint, token ceilings, batching, cheap fallback, code-only where
  possible (review, patterns).
- **Guardrails:** a privacy deny-list before egress, a result gate, read-only SQL, no write tools, idempotent jobs.
- **Memory:** short-term = current session; long-term = persistent file with provenance, evidence counts, temporal
  validity and conflict resolution; episodic = notes/statements; procedural = skills.
- **Evaluation:** code-checkable constraints, turn-depth adherence, cross-session tests, regression replay, baseline
  comparison, human feel first.
- **Observability:** OTel-GenAI spans linked to the context compile; job_run rows; Context viewer.
- **Model selection:** measured per model (availability, effort support, latency); config-driven profiles; prices
  treated as dated facts.
- **Embeddings:** small local model, cosine similarity, a cutoff calibrated from labelled pairs, hybrid with keyword
  search.
- **Agents vs workflows:** use a workflow when the steps are known; use an agent loop only when the path is open-ended,
  and then with draft-first writes.

---------------------------------------------------------------------------------------------------------------------
## 19. Numbers cheat-sheet

| Thing | Number |
|---|---|
| Daily call cap | 25 (free tier), counted from midnight Pacific |
| Context ceilings | chat 8000 / schedule 6000 / extraction 4000 tokens |
| Real chat compile | ~324 tokens (was an unbounded 524k "budget") |
| Per-item cap | 500 tokens; skills ≤2 × ~600; observations ≤3 |
| Persistent-file cap | 1500 tokens; conflicts settle after 3 days |
| Hidden thinking | 817 tokens on the schedule prompt at default effort → 0 at `low` |
| Latency (n=14) | median ~5 s, p90 ~31 s, max 92 s |
| Timeouts | 45 s per attempt, 2 attempts within 130 s |
| Fallback win | 503 at 6.1 s → valid draft at +3.1 s |
| Tokens/char | 3.38 chars/token structured, ~4.0 prose → estimate 3.5 |
| Embeddings | MiniLM-L6-v2, 384-dim, cutoff 0.40 (was 0.65) |
| Patterns | active at 3 occurrences, question after 2 misses, dropped at 3; runs 13:00/21:00 |
| Retention | events 90 d, compile/trace 180 d, heartbeat 400 d |
| Tests | 626+ passing |
| Tables / migrations | ~25 tables, ~25 Alembic migrations |
| Nightly | 9 steps, 20:45 IST + 07:30 retry |
| Eval results | constraint adherence pinned `[__]` vs unpinned `[__]`; cross-session `[__]`; schedule acceptance `[__]` |

---------------------------------------------------------------------------------------------------------------------
## 20. Curveballs and how to handle them

- **"Isn't this over-engineered for one user?"** The constraints were real: a 25-call budget, private data, and a model
  that returns 503s. Each piece answers a measured failure. I also cut things: the classifier, the labelling tool, the
  agent framework and the approval card were all removed when they stopped earning their keep.
- **"How do you know it works?"** Traces, the job_run history, first-draft-vs-final edits, adherence from sessions, the
  eval runs, and daily use. Point to specific numbers, never adjectives.
- **"Why Gemini?"** The free tier made the budget constraint real and forced the discipline; the provider sits behind a
  profile, so switching to Claude/GPT is a config change plus an adapter (the adapter speaks the OpenAI wire format).
- **"What did you get wrong?"** The inherited similarity cutoff; a budget defined only as a fraction; trusting static
  tests for the UI; a synthetic seed in the live DB. Each one now has a fix and a test.
- **"What did AI tools do vs you?"** Be straight about it: I used coding assistants heavily for implementation. I owned
  the architecture, the decisions above, the measurements, the review of every change, and the verification discipline
  (generated code maps so assistants read less; tests and a UI harness so their output is checked).
- **"Single point of failure?"** Supabase (managed, backed up via `pg_dump` before migrations); Actions cron delay →
  morning retry + phone watchdog; model outage → fallback → rule-based draft.
