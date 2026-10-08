# The persistent file

What the assistant always knows about you. It is small on purpose: every entry costs tokens on every prompt, so an
entry earns its place by being true for a long time and useful often. Short-lived facts belong in notes, the schedule
or tasks. A rule that needs steps or a format belongs in a **skill**, not here (see ASIDE_PLAN.md §3.4: if it fits in
one sentence it is a persistent-file entry; if it is a procedure or a format it is a skill).

Schema: [`context/persistent_schema.py`](../context/persistent_schema.py). Empty starter: [`persistent_file.starter.json`](persistent_file.starter.json).

## Sections

| Section | Holds | In every prompt? |
|---|---|---|
| `identity` | Identity and stable facts: name, city, work, things that do not change | yes |
| `preferences` | Hard preferences and constraints: how things must be done, what must never happen | yes |
| `routine` | Routine skeleton: the usual weekday and weekend shape of the day | yes |
| `goals` | Goals and current focus: what you are working toward now | chosen per situation |
| `people` | People who matter, and what the assistant should know about them | chosen per situation |

"Chosen per situation" means the recipe decides (selector `rules`), or, with the selector `off`, everything is
included and the packer's budget decides. Observations (patterns the code finds) are a separate context source and
never live in this file.

Stored in the database, one row per entry (`persistent_entry`: the six fields below plus `section`, `retired_reason`,
`replaces_id` and `seen_days` for bookkeeping). The old two-bucket `static`/`dynamic` table was dropped in M3a.

## Entries

Exactly these fields, nothing else:

| Field | Meaning |
|---|---|
| `text` | One plain sentence, 500 characters at most. Whitespace is tidied. |
| `source` | `user` (you wrote it), `agent` (the assistant wrote it, fairly confident), `note` (promoted from a note), `pattern` (from a detected pattern) |
| `created` | When it was added (timezone required) |
| `last_confirmed` | When it was last seen to still be true, or `null`. Never earlier than `created`. |
| `evidence_count` | How many times it has been confirmed; at least 1 |
| `status` | `active`, `provisional` or `retired` |

A section may not hold two live entries with the same text (case-insensitive). A retired entry may repeat one.

## Status and conflicts (ASIDE_PLAN.md §3.3)

- A new fact that conflicts with an old one is added as `provisional` with `replaces_id` pointing at the old entry, and
  both are kept. Code cannot judge meaning pre-API, so the conflict is stated explicitly: the editor's "Replaces" picker
  (later, the agent's write). An entry written with `source` `note` is also provisional until repeated.
- A **sighting** is the same text added again, or "Still true" in the editor. It adds the day (IST) to `seen_days`,
  raises `evidence_count` and sets `last_confirmed`. Several sightings on one day count as one day.
- When a provisional entry has been seen on `settle_days` separate days (config, default 3) it becomes `active` and the
  entry it replaced becomes `retired` (reason `replaced`). Retired entries stay in the table and the log but never reach a
  prompt. Retiring a provisional entry yourself is the "contradiction": it never settles.
- The user and, when fairly confident, the assistant may write to any section. Every write is one row in `diff_log`
  (before and after) and can be undone from What changed. Undo is refused if the entries changed since; the undo is
  itself logged and cannot be undone. Editing text keeps `created`.
- When the live entries outgrow `cap_tokens` (config, default 1500), entries are retired with reason `evicted`: `goals`
  and `people` first (`evict_first`), then least `evidence_count`, then longest without confirmation. The entry just
  written is never the one evicted. Each eviction is logged and undoable.

All of these numbers live in `config.toml` under `[memory]`; they are starting points to tune by experiment.
`excitement` is kept there for later pattern code and is not used yet.

## Writing good entries

- One fact per entry. "Lives in Pune" and "works at night" are two entries.
- State it as true now, not as a plan: "Plays tennis on Saturday mornings", not "Wants to play tennis more".
- Hard constraints are phrased so a checker could verify them: "Replies in under 80 words unless asked for detail".
- Do not put secrets here. Everything in this file is sent to the model provider with each prompt.

## Example (shape only; not real data)

```json
{
  "version": 1,
  "sections": {
    "identity": {"entries": [
      {"text": "Lives in Pune and works in IST.", "source": "user", "created": "2026-10-06T10:00:00+05:30",
       "last_confirmed": null, "evidence_count": 1, "status": "active"}
    ]},
    "preferences": {"entries": []},
    "routine": {"entries": []},
    "goals": {"entries": []},
    "people": {"entries": []}
  }
}
```
