# What the Gemini API actually does (measured, not assumed)

Measured on **2026-10-07, 18:00-18:50 IST**, free tier, `scripts/api_probe.py` (every probe went through the approval/quota
gate and is in `llm_trace`; the raw results are in `data/api_probe/results.jsonl`). Prompts were synthetic. **22 of the day's
25 calls** were used: 18 probes, 2 earlier (the user's chat call and the first M6c attempt), 2 in the final live check. Re-run any experiment with `scripts/api_probe.py --run NAME`.

**Read the numbers as a trend, not a proof.** They are small samples, taken while the service was visibly busy, by one user on
one free-tier project. Where a conclusion needs more data, it says so.

## 1. Thinking is hidden, it costs tokens, and it competes with the answer

Every Gemini 3.x Flash model thinks by default and **cannot turn it off** (the lowest setting is `low`/`minimal`). The API does
not report thinking tokens: `completion_tokens_details.reasoning_tokens` is absent. The only trace is the gap
`total_tokens - prompt_tokens - completion_tokens`.

| prompt | effort | prompt | visible out | **hidden thinking** | finish |
|---|---|---|---|---|---|
| "Reply with exactly: OK" | default | 9 | 1 | **104** | stop |
| same, `max_tokens=50` | default | 9 | **0** | 46 | **length** |
| same | low | 9 | 1 | 64 | stop |
| same | minimal | 9 | 1 | **0** | stop |
| schedule request (531 tokens) | default | 531 | 83 | **817** | stop |
| schedule request | low | 531 | 79 | 0 | stop |
| chat request (71 tokens) | low | 71 | 106 | 284 | stop |

- **Thinking counts inside `max_tokens`.** With a cap of 50 the visible answer was *empty* (`finish=length`): 46 hidden tokens
  used the budget. This is what happened to the user's own chat call earlier the same day (77 visible tokens, `finish=length`,
  cap 1000, so roughly 920 hidden).
- `reasoning_effort` works through the OpenAI-compatible endpoint (`minimal|low|medium|high`). **Which values a model accepts
  differs**: `gemini-3.7-flash` answers `minimal` with HTTP 400 ("Thinking level MINIMAL is not supported for this model").
- Default (medium) thinking used 817 hidden tokens on a task that needs ~80 visible ones, with no visible quality gain: every
  successful reply at every setting parsed and validated the same way (2 sensible blocks).

## 2. Latency is about server load, not about the request

Successful call latencies (n = 14, seconds): 1.6, 1.7, 2.1, 2.2, 2.6, 3.0, 3.1, 4.8, 7.4, 7.4, 22.0, 26.5, 31.0, **92.3**.
Median ~5 s, p90 ~31 s, max 92 s. The 92 s call had **0 hidden thinking tokens**; the 2.6 s call had a similar prompt. Failures:

| what | where |
|---|---|
| HTTP 503 "This model is currently experiencing high demand" | `gemini-3.8-flash` **2 of 2** attempts, instantly (1.4 s and 2.6 s); `gemini-3.6-flash` twice (after 33 s and 6 s) |
| no answer within 60 s / 150 s | `gemini-3.6-flash`, default thinking, twice (the original M6c call at 61 s; a chat probe at 150 s) |
| HTTP 400, effort not supported | `gemini-3.7-flash` + `minimal` |

- A 503 on one model **persists for minutes** (3.8-flash failed both attempts, about 20 minutes apart), so retrying the same model is
  the wrong response; a different model answered in about 3 s both times we tried (`gemini-3.1-flash-lite`, 2 of 2).
- **Default thinking correlated with the failures**: 1 clean success in 5 attempts, against 8 of 9 at low/minimal. Confounded by
  time of day and load; treat as a lead, not a result.
- The `nojson` probe (no `response_format`) returned the JSON inside a markdown code fence. `json_object` mode returned bare JSON
  every time, and strict `json_schema` mode also works. The app's parser tolerates fences either way.

## 3. Quota

- Limits are **per project** and the daily one resets at **midnight Pacific** (12:30 IST in US daylight time, 13:30 IST in winter).
- Google's docs do not publish the free-tier numbers (they are in AI Studio), and **no response header reports remaining quota**
  (checked: only `server-timing` and generic headers). So the remaining allowance cannot be read from the API; the local counter is
  the only instrument. The docs do not say whether failed requests count; the app assumes they do.

## 4. Estimating tokens

A structured schedule prompt (1793 characters) was **531 real tokens = 3.38 chars/token**; chat prose was ~4.0. The old estimate
(4.0 chars/token x 1.15) gave 525 for the schedule prompt: *below* the truth. Gemini profiles now use 3.5.

## 5. What the app does about it (and the test that pins each)

| finding | change | test |
|---|---|---|
| thinking is hidden | `Usage.thinking_tokens` (= total - prompt - completion), billed as output, in every trace | `test_api_design.py` |
| thinking eats `max_tokens` | `max_tokens` 3000 (schedule) / 2000 (chat); `finish_reason=length` reported in the API reply, the trace (`truncated`) and the card | `test_llm_approval.py` |
| default thinking is slow and fragile | `reasoning_effort` per model in `config/models.toml` (`low` for 3.6 Flash), sent on every call | `test_api_design.py` |
| per-model effort support | the value lives in the profile with its evidence; a test forbids `minimal` on 3.7 | `test_api_design.py` |
| 503s and stalls | one fallback model (`[llm] fallback_model`), tried on 5xx / timeout / 429 only, never on 400/401; each attempt gated, counted and traced | `test_api_design.py` |
| stalls of 60-150 s | per-attempt timeout 45 s (`[llm] attempt_timeout`, max 60) so two attempts fit inside the panel's 130 s | `test_approved.py` |
| waiting feels frozen | the card counts seconds and, after 15 s, says why | `scripts/ui_check` |
| quota day | the cap counts from midnight Pacific (`llm/quota.py`) | `test_api_design.py` |
| token estimate low | 3.5 chars/token for Gemini | `test_api_design.py` |

**Validated on the real API** with the new design: `gemini-3.6-flash` (low) answered 503 after 6.1 s, the call fell back to
`gemini-3.1-flash-lite`, which returned a valid two-block draft 3.1 s later; both attempts were traced, with their efforts.

## 6. Not decided, because the data does not settle it

- **Is `low` better than `minimal` for 3.6 Flash?** Both worked; latency differences were swamped by load noise.
- **Does the fallback give worse drafts?** On this easy prompt the drafts were equivalent (the lite model proposed a duplicate of
  a *fixed* block once; the validator rejected it, which is what the validator is for). A real quality comparison needs an eval
  set (M8).
- **Streaming** was not tried: the stalls happen before the first byte, so it would not obviously help.
- **`gemini-3.8-flash` and `3.7-flash`** were mostly unreachable (503) or rejected the setting, so they were not characterised.
- **Rate limits (RPM/TPM/RPD)** were not hit and not measured. Do not infer a limit from "22 calls worked".
- **Temperature**: Google's docs say nothing; 0.3 (schedule) and 0.7 (chat) worked.
