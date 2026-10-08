# Aside Android app: plan

Companion to the cloud plan (`C:\Users\akash\.claude\plans\1-i-don-t-normally-delegated-flurry.md`; read it and ASIDE_PLAN.md first). This file is the handoff for building the phone app. It records what was decided and why, so a new session does not re-ask.

## 1. What the app is for

The phone is a **sensor and a capture tool**, not a brain. The brain is the nightly GitHub Action; the database is Supabase. The app:
1. captures notes, journal entries and tasks instantly, from any screen, with the least possible friction;
2. records foreground-app usage (no titles/URLs) for the day's review;
3. lets the user find, edit, delete and convert what they captured (word search over ALL their notes, including ones written on the laptop);
4. acts as a watchdog and failure notifier for the nightly job.
It deliberately shows NO schedule and NO task list (Google Calendar and Google Tasks do that).

User: Akash. Phone: **Moto G42, Android 13 (API 33)**, always on, always online, stock-like Motorola Android. Dev machine: Windows 11, Android Studio installed at `C:\Program Files\Android\Android Studio` (JBR at `...\jbr`), SDK at `%LOCALAPPDATA%\Android\Sdk` (platform `android-37`, build-tools `36.0.0`, platform-tools has adb). Style: Material 3 dark (themes later). "Never in the way": no streaks, no nudges, no daily summaries.

## 2. Decisions (settled)

Capture (the heart of the app; the user's own earlier app `quick-capture-app` on GitHub is the idea source, NOT code to copy: it saves to CSV and has no sync. Reuse its good tricks: draggable overlay bubble, `TYPE_APPLICATION_OVERLAY`, a foreground service with a minimal-importance notification, editor window without `FLAG_NOT_FOCUSABLE`, and the keyboard-raising retries for stubborn devices)
- A small floating **dot**, always on screen: snaps to the screen edge, fades when idle, remembers its position. Drawn by a foreground service.
- **Tap** opens a small input window at the top of the screen, keyboard already up, cursor in the box.
- **No save button.** Text is stored on the phone as it is typed (Room `item` row, state `open`). The item is **committed** when the window is dismissed (tap outside, Back, swipe); empty text is discarded. A short haptic tick + brief flash on the dot confirms. If the process dies or the window is abandoned, the open text is committed on next start (never lost). Save is final; changes are made inside the app later.
- **Type chips** above the box: Note / Journal / Task, default **Note every time** (not sticky: a sticky Journal would make private-by-accident captures). Prefixes also switch type: `j:`/`journal:`, `t:`/`task:` (same keyword idea as `capture/router.py`; prefix is stripped).
- **Task** shows an optional due row (Today / Tomorrow / pick / none). Never compulsory. (Server-side the LLM judges urgency.)
- **Paste clipboard** button in the input window (user-triggered, so no silent clipboard reads).
- **Hide the dot:** drag it to an X at the bottom. About **15 minutes later ONE quiet notification** appears ("Bring back quick capture"); tapping it restarts the dot. It does not repeat. Turning capture off in Settings cancels the reminder.
- **Other entry points:** a Quick Settings tile, "Share to Aside" (share sheet), home-screen icon shortcuts (New note / journal / task).
- **Voice:** long-press on the dot starts Android's built-in `SpeechRecognizer`; the words are written into the box and committed when the user stops speaking. Needs RECORD_AUDIO. Built late (it does not affect the rest).
- **Convert** a note to a task (and back) inside the app.
- NOT included (user said no): context tag ("taken in Maps"), photo notes, export, "remember this", streaks/summaries, reply-box notification, per-item "private" flag, pause switch. Revisit later only if asked.

Retrieval and editing (in the app, behind the lock)
- Screens: **Notes** (recent first, with **word search over all notes** incl. laptop-written ones), item screen (edit text, delete, convert note<->task), **Status**, **Settings**.
- Word search = a database function that matches ANY word case-insensitively (same idea as `search_notes(match_any=True)` in `knowledge/notes.py`) and returns date + snippet + text; phone merges its own unsent items. Needs internet (always available). No semantic search on the phone (needs the embedder).
- Edits/deletes sync through the same client ids. Journal entries appear in search only when unlocked.

Lock (user: "anyone can add via the dot, only I can read")
- The dot and input window are **never locked** and show nothing from before (an empty box), so a stranger can add but not read.
- Opening the app requires **BiometricPrompt** (fingerprint, falling back to device PIN via `DEVICE_CREDENTIAL`). Stays unlocked ~2 minutes after leaving the app (no repeated prompts).
- **Toggle in Settings**; changing it requires passing the lock first.
- Notifications never contain note text. The Room database stays unencrypted in v1 beyond Android's app sandbox (encrypt later only if wanted).

Sync, auth and background work
- **Sign in once** with Supabase Auth (email + password); the session persists and refreshes itself; never prompted again. (The website uses the same login.)
- The phone calls **database functions (RPCs)**, not tables, so the phone does not know the table layout. Functions run as the signed-in user; `anon` has no execute rights.
- Items carry a **client id (UUID)** made on the phone and the phone's own timestamp; retries can never duplicate.
- **One WorkManager job (~every 15 min, plus an expedited run right after each commit)** does: (1) send committed items, applies edits/deletes; (2) read new foreground-app sessions via `UsageStatsManager` and send them; (3) **watchdog**: if between 21:10 and ~22:30 IST, tomorrow has no generated schedule, and it has not already triggered today, dispatch the GitHub workflow (fine-grained token limited to dispatching that one workflow, stored in encrypted storage); (4) **failure notification**: poll for a final job failure and show a quiet notification with a one-line reason, only after the retry also failed. No exact-alarm permission is needed.
- Usage: foreground only, package name + start/end (map package to app label in the dashboard), ignore launcher/system UI and sessions under a few seconds, skip an **ignore list** (banking apps by default, editable in Settings), dedupe key = hash(package + start), cursor-based so late flushes lose nothing. The pause switch is NOT in v1.

## 3. Database contract (built in the cloud plan's Supabase step; the phone depends on it)

Migration (after a dump + the user's go-ahead): add `client_id uuid unique` to `notes` and `tasks` (and `kind`/`source` handling for journal); statement row written for journal/notes exactly like `capture/server.py` (~line 673). Existing columns: `tasks(id, text, due_at, status, created_at, completed_at)`; `notes(id, text, created_at, source, ...)`; `events(... source, kind, app, ts_start, ts_end, payload, dedupe_key)`. Check `storage/models.py` before writing SQL.

RPCs (names indicative), all `security definer`, `authenticated` only, `anon`/`public` revoked:
- `capture_note(client_id, text, kind, created_at)` -> inserts note (+ statement), idempotent on `client_id`.
- `capture_task(client_id, text, due_at, created_at)` -> idempotent.
- `update_note(client_id, text)`, `delete_note(client_id)`, `convert_note_to_task(client_id, due_at)`, `convert_task_to_note(client_id)`.
- `search_notes(query, limit)` -> any-word, case-insensitive, newest first; returns client_id/id, kind, created_at, snippet, text.
- `upload_usage(batch jsonb)` -> inserts `events` rows with `source='android'`, `ON CONFLICT (dedupe_key) DO NOTHING`.
- `schedule_ready(day date)` -> bool (a generated schedule exists for the day).
- `latest_failure()` -> last `job_run` failure that survived its retry, with reason, or null.
Enable RLS on every table with no anon policies (cloud plan section 4, #11); the phone never touches tables directly.

## 4. Tech

Kotlin; Jetpack Compose + Material 3 for the app screens; the **overlay (dot + input window) uses Android Views** drawn with `WindowManager` from a service (Compose inside a service overlay needs lifecycle workarounds; not worth it); Room (items, open draft, outbox state); WorkManager; androidx.biometric; Supabase Kotlin client (auth + postgrest rpc) or plain Ktor/OkHttp + kotlinx.serialization (decide in M3; prefer the one that gives least code); encrypted prefs for the GitHub token. Package `com.akash.aside`, app name "Aside", minSdk 33 (the user's Moto G42 runs Android 13 and the app is only for their own phone, so no older-version code), targetSdk 34 or 35 (declare a foreground-service type, `specialUse`, since target 34+ requires one), compileSdk = what the installed SDK has (36/37; check `platforms/`). **The project is its own git repo (`aside-android`, public like this one) at `C:/Users/akash/AndroidStudioProjects/aside`, outside OneDrive. It exists (Empty Activity, Compose, AGP 9.4.1, Kotlin 2.2.10, compileSdk/targetSdk 37, minSdk 33) and builds.**

Permissions: SYSTEM_ALERT_WINDOW ("Display over other apps", manual grant), FOREGROUND_SERVICE (+ type), POST_NOTIFICATIONS (Android 13 runtime), PACKAGE_USAGE_STATS ("Usage access", manual grant), RECORD_AUDIO (voice, later), INTERNET, USE_BIOMETRIC, RECEIVE_BOOT_COMPLETED (restart the dot after reboot). A Settings checklist shows which grants are missing with a button to each system screen. Also ask the user to exempt the app from battery optimisation.

Repo: separate repo outside OneDrive (decided 2026-10-09): no Gradle/OneDrive churn and no `.gitignore` changes in the Python repo. The generated `.gitignore` already ignores `.gradle`, `/build`, `local.properties`; also add `*.jks`, `*.keystore` and any file holding secrets. Build from the command line with `JAVA_HOME="C:/Program Files/Android/Android Studio/jbr"` and `./gradlew assembleDebug` (first build ~6 min); install with `%LOCALAPPDATA%/Android/Sdk/platform-tools/adb install -r app/build/outputs/apk/debug/app-debug.apk`. The Moto G42 (adb id `ZD2226Q4Z3`) is detected over USB. The database contract lives in the Python repo (`aside`), and this plan stays here as the shared reference. Never commit keys, Supabase URL/anon key, or the GitHub token (use `local.properties`/`BuildConfig` from a git-ignored file, or enter them in Settings once).

## 5. Milestones, in order

Milestones M1-M2 need no Supabase and can start before the cloud plan reaches its Supabase step. Each ends with the app installed on the real phone and the user trying it.

- **M0 Project + first run (user, guided by Claude).** Create the Empty Activity project (done, at the path above), sync, enable USB debugging on the Moto G42, run on the phone. Claude checks `gradlew` builds from the command line using the Studio JBR (`JAVA_HOME`) and that `adb devices` lists the phone. DONE 2026-10-09: builds, installs and launches on the phone.
- **M1 Capture core (local only).** Room schema (`item`: client_id, kind, text, due_at, state open/committed/synced, created_at, updated_at, deleted); foreground service + dot (snap, fade, remembered position, drag to X to hide); input window (autosave on every keystroke, commit on dismiss, chips, prefixes, due row for tasks, paste-clipboard button, haptic tick); crash/kill recovery of open text; 15-minute one-time reminder notification; start after boot; Quick Settings tile, share target, icon shortcuts. Unit tests for prefix parsing, commit rules, recovery; manual checks on the phone (keyboard raising, over several apps, rotation, killing the app).
- **M2 In-app screens (local data).** Compose theme (Material 3 dark); Notes list with word search over local items; item screen (edit, delete, convert note<->task); Status screen; Settings (permission checklist, ignore list, capture on/off); the lock (BiometricPrompt, 2-minute grace, toggle). Tests for search and lock grace logic.
- **M3 Sync (needs Supabase, the Auth user and the RPCs).** Sign-in once; outbox worker with retry/backoff and idempotency; expedited run after commit; Status shows queue, last upload. Tests with a fake backend; offline-then-online and "send same batch twice" checks; RLS check that the anon key cannot call anything.
- **M4 Cloud search and edit sync.** `search_notes` merged with local unsent items; edits/deletes/conversions pushed; notes written on the laptop appear in search.
- **M5 Usage collector.** `UsageStatsManager` reader with cursor, ignore list, dedupe keys, batch upload to `upload_usage`; verify rows land with `source='android'` and a known app session (e.g. open YouTube for a minute).
- **M6 Watchdog + failure notification.** The 21:10 check and workflow dispatch with the limited token; failure poll and notification (only after the retry also failed); tests with a fake clock and fake backend. Needs the GitHub workflow from the cloud plan.
- **M7 Voice.** Long-press on the dot -> `SpeechRecognizer` -> text in the box -> commit on silence; handle no-permission and no-recognizer cases.
- **M8 Polish.** Battery-optimisation prompt, edge cases found while using it for a week, themes later.

## 6. Verification
- Unit tests (JVM) for pure logic; instrumented/manual checks on the Moto G42 for everything that touches the overlay, keyboard, permissions, background work.
- Real-device checklist per milestone (written in the milestone's PR/commit notes): dot visible over Chrome/Maps/YouTube; input window opens with the keyboard; kill the app mid-typing and confirm the text is recovered; airplane mode capture then reconnect delivers exactly once; lock prompts on opening the app but never on the dot; reboot restarts the dot.
- Sync: counts match between phone and Supabase after a burst of captures with the network flapping.
- Screenshots via `adb exec-out screencap` when judging the UI.

## 7. Risks
- Motorola/Android background limits can delay WorkManager or kill the service: the foreground service and the battery exemption mitigate it; the cursor-based usage reader tolerates late flushes.
- Keyboard focus in an overlay is finicky across devices (hence the retry tricks from the old app); test on the real phone early (M1).
- Overlay permission and Usage access are manual grants; the Settings checklist must make them one tap away.
- SpeechRecognizer availability varies; voice must degrade quietly.
- The GitHub token on the phone is a secret: scope it to one workflow's dispatch, store it encrypted, and make it revocable.
- Cloud dependencies (Supabase project, RPC migration, workflow) must exist before M3/M6; M1-M2 deliberately do not need them.
