# Eval suite

Run the synthetic harness from the repository root:

```powershell
python -m evals.run
python -m evals.run --pinning on --scenario constraint_carryover
python -m evals.run --output data/eval_report.json
```

The default path uses `FakeClient`, checks the constraint scenario at turns 5, 10, 15 and 20, and checks a preference in a new session. Pinning is introduced only after the constraint has been stated, and only in session 2 for the cross-session scenario. Its canned responses check the harness and report format; they are not model-quality results. `questions.json` contains the separate 15-question standard set for later judged runs.

To opt into a real model run, select one or two synthetic scenarios and one pinning setting explicitly:

```powershell
python -m evals.run --model gemini-3.6-flash --scenario constraint_carryover --pinning on --output data/eval_constraint_on.json
python -m evals.run --model gemini-3.6-flash --scenario vegetarian_preference --pinning on --output data/eval_report.json
python -m evals.run --model gemini-3.6-flash --question q01 --question q06 --output data/eval_questions.json
```

The model option never reads personal records. Scenario runs use only `scenarios.json`; qualitative runs use only selected rows from `questions.json`. Every attempt goes through the ordinary `ApprovedClient` gate, is quota checked, and is traced. The harness refuses a run unless the provider-day budget can cover the selected inputs while preserving five calls of headroom. The fallback is disabled so the planned call count stays bounded. A constraint scenario uses 20 calls; the cross-session scenario uses two; qualitative runs accept up to three questions. Their report saves the response, review criteria, and blank `feel_rating` / `review_notes` fields. Judge the overall feel first; criteria are prompts for reflection, not automatic scores. Real runs require a live database for the call counter and trace rows. Do not select `--pinning both` for a real run; compare the two settings with separate, deliberately rationed runs.

`run_suite` also accepts a client factory for controlled embedding or test use. Any supplied client must be `ApprovedClient`, created through `llm.factory.create_client`; each request gets a user grant and is subject to the configured quota and trace. The `wrong:` replay command is local context replay only: stored compile inputs may contain personal text, so it deliberately sends no model requests.

```powershell
python -m evals.replay_wrong
python -m evals.replay_wrong --recipe chat --output data/wrong_replay.json
```
