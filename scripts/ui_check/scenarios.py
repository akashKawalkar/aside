# scripts/ui_check/scenarios.py — what a person does with the schedule-draft UI, driven in real headless Chrome.
# Each scenario gets (browser, urls, shots) and raises AssertionError on failure; it prints one "ok" line per check.
# `urls`: panel, settings, api. Screenshots go into `shots` so the look can be judged, not just the DOM.
from __future__ import annotations

ok = lambda message: print(f"    ok   {message}")


def no_js_errors(*pages):
    for page in pages:
        if page is not None:
            assert not page.errors, f"JavaScript errors on the page: {page.errors}"


async def main_flow(b, urls, shots):
    """Ask for a draft (the model is called at once), accept / edit / revise, see it in settings, accept the rest."""
    panel = settings = None
    try:
        panel = await b.page(urls["panel"], 420, 900)
        await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the Draft tomorrow link")
        assert await panel.text("#tile-schedule-draft .draft-link") == ["Draft tomorrow"]
        await panel.shot(shots / "1_link.png"); ok("the link is shown when tomorrow is empty")

        await panel.click("#tile-schedule-draft .draft-link")
        await panel.type(".draft-form input", "zz keep the evening quiet")
        await panel.click(".draft-form .btn", "Draft with model")
        await panel.wait("document.querySelectorAll('.draft-list .draft-entry').length === 3", what="3 draft entries")
        assert await panel.text(".draft-entry .event-title") == ["zz Gym", "zz Deep work", "zz Tennis"]
        assert await panel.js("document.querySelectorAll('#tile-schedule-draft .event-reason').length") == 0
        await panel.shot(shots / "4_draft.png"); ok("three blocks, and no reasons in the panel")

        await panel.click(".draft-entry:nth-child(1) .draft-entry-actions button[aria-label='Add to my schedule']")
        await panel.wait("document.querySelectorAll('.draft-badge').length === 1", what="Added badge")
        ok("accepting a block marks it Added")

        await panel.click(".draft-entry:nth-child(2) .event-body")
        await panel.wait("document.querySelector('.event.editing')", what="inline editor")
        await panel.type(".event.editing input[aria-label='Title']", "zz Deep work (edited)")
        await panel.click(".event.editing .btn", "Save")
        await panel.wait("document.querySelectorAll('.draft-entry .event-title')[1]?.textContent === 'zz Deep work (edited)'", what="edited title")
        ok("a block can be edited inline")

        await panel.click(".draft-actions .btn", "Revise")
        await panel.type(".draft-form input", "zz make the evening quieter")
        await panel.click(".draft-form .btn", "Revise with model")
        await panel.wait("document.querySelectorAll('.draft-list .draft-entry').length === 3", what="revised draft")
        rows = await panel.text(".draft-entry")
        assert "zz Gym" in rows[0] and "Added" in rows[0] and "zz Deep work (edited)" in rows[1] and "zz Reading" in rows[2], rows
        await panel.shot(shots / "6_revised.png"); ok("a revision keeps the accepted and edited blocks and redoes the rest")

        settings = await b.page(urls["settings"] + "#review", 1100, 900)
        await settings.wait("!document.getElementById('review-draft-group').hidden", what="the Tomorrow card on the review page")
        assert any("quieter evening" in r for r in await settings.text("#review-draft-card .event-reason"))
        await settings.shot(shots / "7_review.png"); ok("the review page shows the open draft, with reasons")

        await settings.goto(urls["settings"] + "#drafts")
        await settings.wait("document.querySelectorAll('#drafts-list .card').length >= 3", what="the drafts tab")
        await settings.wait("[...document.querySelectorAll('#drafts-list .supporting')].some(e => e.textContent.includes('kept as proposed'))", what="comparison")
        body = " | ".join(await settings.text("#drafts-list"))
        assert "Version 2" in body and "Version 1" in body and "superseded" in body and "zz keep the evening quiet" in body, body[:500]
        assert "You changed it to: zz Deep work (edited)" in body
        await settings.shot(shots / "8_drafts.png"); ok("the drafts tab lists both versions, what the model proposed, and the comparison")

        await panel.click(".draft-actions .btn", "Add all")
        await panel.wait("document.querySelector('#tile-schedule-draft .draft-section[hidden]')", what="the section hides once settled")
        rows = await panel.js(f"fetch('{urls['api']}/schedule?date=2031-03-04').then(r => r.json()).then(j => j.data.entries.map(e => e.title + '|' + e.origin))")
        assert sorted(rows) == ["zz Deep work (edited)|generated", "zz Gym|generated", "zz Reading|generated"], rows
        ok("accepting everything hides the section and leaves three generated schedule rows")

        # The Schedule settings tab badges them (the settings page uses the real clock, so jump to the fixture day).
        await settings.goto(urls["settings"] + "#schedule")
        await settings.wait("document.querySelector('#schedule-view')", what="the schedule tab")
        await settings.js("import('./tabs/schedule.js').then(m => m.setScheduleDay(new Date(2031, 2, 4)))")
        await settings.wait("document.querySelectorAll('#schedule-view .event-badge.generated').length === 3", what="three Generated badges")
        badges = await settings.text("#schedule-view .event-badge.generated")
        assert all(b.startswith("Generated") for b in badges), badges
        await settings.shot(shots / "9_schedule_badges.png"); ok("the Schedule tab marks generated entries, and those the user edited")
        no_js_errors(panel, settings)
    finally:
        pass


async def chat_card(b, urls, shots):
    """Chat answers at once: Enter sends, the reply appears in the thread, no card in between."""
    panel = await b.page(urls["panel"], 420, 900)
    await panel.wait("document.getElementById('composer-input')", what="the composer")
    await panel.type("#composer-input", "zz what is on my plate")
    await panel.wait("!document.getElementById('send-button').disabled", what="send enabled")
    await panel.click("#send-button")
    await panel.wait("document.querySelectorAll('#chat-thread .chat-row.agent').length === 1", what="the reply")
    assert await panel.js("!document.querySelector('#chat-thread .approval-card')")
    reply = (await panel.text("#chat-thread .chat-row.agent"))[0]
    assert "zz Gym" in reply, reply[:200]          # the fake model's scripted reply
    await panel.shot(shots / "10_chat_reply.png"); ok("a chat message is answered straight away, with no model call")
    no_js_errors(panel)


async def rule_based_and_failure(b, urls, shots):
    """Needs the server started with UI_BAD=1: the rule-based draft (no model), discard, and an unusable reply."""
    panel = await b.page(urls["panel"], 420, 900)
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link")
    await panel.click("#tile-schedule-draft .draft-link")
    await panel.click(".draft-form .btn", "Rule-based draft")
    await panel.wait("document.querySelectorAll('.draft-list .draft-entry').length === 2", what="copied entries")
    assert await panel.text(".draft-entry .event-title") == ["zz Gym", "zz Deep work"]
    assert (await panel.text(".draft-head"))[0].endswith("rule-based")
    await panel.shot(shots / "11_rulebased.png"); ok("the rule-based draft copies last Tuesday with no model call")

    await panel.click(".draft-actions .btn", "Discard draft")
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link again")
    ok("discarding returns to the quiet link")

    await panel.click("#tile-schedule-draft .draft-link")
    await panel.click(".draft-form .btn", "Draft with model")
    await panel.wait("document.querySelector('.snackbar')?.textContent.includes('could not be used')", what="failure text")
    await panel.shot(shots / "12_unusable.png")
    await panel.click(".draft-form .btn", "Cancel")
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link after a failure")
    assert await panel.js(f"fetch('{urls['api']}/schedule/draft/status').then(r => r.json()).then(j => j.data.draft === null)")
    ok("an unusable reply gives a plain message and creates no draft")
    no_js_errors(panel)


TABS = ["general", "tasks", "task-history", "schedule", "drafts", "observations", "review", "inspect", "context", "data-gaps",
        "persistent-file", "skills", "statements", "notes", "tables", "ring-log"]
BAD_WORDS = ("failed to load", "couldn't load", "could not load", "unknown icon", "undefined", "[object object]")


async def settings_tabs(b, urls, shots):
    """Open every settings tab against the real data and report what each one shows (read-only apart from the fixtures)."""
    page = await b.page(urls["settings"], 1100, 900)
    await page.wait("document.querySelector('.nav-item.active')", what="settings start-up")
    problems = []
    for tab in TABS:
        await page.goto(f"{urls['settings']}#{tab}")
        await page.js("new Promise(r => setTimeout(r, 1800))")            # let the tab's loader finish
        text = (await page.js(f"document.getElementById('tab-{tab}').innerText")) or ""
        flat = " ".join(text.lower().split())
        bad = [w for w in BAD_WORDS if w in flat]
        await page.shot(shots / f"tab_{tab}.png")
        status = "PROBLEM " + str(bad) if bad else "ok"
        print(f"    {status:<10} {tab:<16} {len(text):>5} chars   {flat[:90]!r}")
        if bad:
            problems.append((tab, bad))
    assert not problems, f"tabs showing an error: {problems}"
    no_js_errors(page)


async def tasks_undo_and_presets(b, urls, shots):
    """Complete a task and undo it from the snackbar; the due picker offers one-click presets."""
    settings = await b.page(urls["settings"] + "#tasks", 1100, 900)
    made = await settings.js(f"fetch('{urls['api']}/tasks', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{text: 'zz undo me'}})}}).then(r => r.json()).then(j => j.data.id)")
    await settings.goto(urls["settings"] + "?reload=1#tasks")
    row = "[...document.querySelectorAll('#tasks-view .gt-row')].find(r => r.querySelector('.gt-title').value === 'zz undo me')"
    await settings.wait(row, what="the new task")

    await settings.js(f"{row}.querySelector('.chip').click()")
    await settings.wait("document.querySelectorAll('.due-presets .chip').length >= 1", what="due presets")
    labels = await settings.text(".due-presets .chip")
    assert set(labels) <= {"This evening", "Tomorrow", "Next Monday"} and "Tomorrow" in labels, labels
    await settings.shot(shots / "13_due_presets.png"); ok("the due picker offers one-click presets")
    await settings.click(".due-presets .chip", "Tomorrow")
    await settings.wait(f"{row}?.querySelector('.chip').textContent.includes('Tomorrow')", what="the due date to change")
    ok("choosing a preset saves it at once")

    await settings.js(f"{row}.querySelector('input[type=checkbox]').click()")
    await settings.wait(f"!({row})", what="the task to leave the list")
    await settings.click(".snackbar .btn", "Undo")
    await settings.wait(row, what="the task to come back")
    status = await settings.js(f"fetch('{urls['api']}/tasks?limit=500').then(r => r.json()).then(j => j.data.items.filter(t => t.id === {made}).length)")
    assert status == 1
    ok("Undo on the completed snackbar puts the task back")
    no_js_errors(settings)


async def find_notes(b, urls, shots):
    """Look notes up from the panel (`find:`) and from the Notes tab, locally: no model call, nothing saved."""
    panel = await b.page(urls["panel"], 420, 900)
    await panel.wait("document.getElementById('composer-input')", what="the composer")
    for text in ("note: zz tennis with Rahul on Friday", "note: zz buy oat milk"):
        await panel.type("#composer-input", text)
        await panel.wait("!document.getElementById('send-button').disabled", what="send enabled")
        await panel.click("#send-button")
        await panel.wait("document.getElementById('composer-input').value === ''", what="the note to be saved")

    await panel.type("#composer-input", "find: zz tennis")
    await panel.click("#send-button")
    await panel.wait("document.querySelector('#chat-thread .find-note')", what="found notes in the thread")
    found = await panel.text("#chat-thread .find-note-text")
    assert found == ["zz tennis with Rahul on Friday"], found
    assert await panel.js("!document.querySelector('#chat-thread .approval-card')")
    await panel.shot(shots / "14_find.png"); ok("find: lists matching notes in the thread, with no model call")

    settings = await b.page(urls["settings"] + "?find=1#notes", 1100, 900)
    await settings.wait("document.querySelectorAll('#notes-view .note-row').length >= 2", what="the notes list")
    await settings.type("#notes-search", "zz oat")
    await settings.wait("[...document.querySelectorAll('#notes-view .note-row input')].every(i => i.value.includes('oat')) && document.querySelectorAll('#notes-view .note-row').length === 1", what="the filtered list")
    await settings.shot(shots / "15_notes_search.png"); ok("the Notes tab searches locally and shows only the match")
    no_js_errors(panel, settings)


# name -> (function, environment for the test server)
SCENARIOS = {
    "main": (main_flow, {}),
    "chat": (chat_card, {}),
    "edge": (rule_based_and_failure, {"UI_BAD": "1"}),
    "tabs": (settings_tabs, {}),
    "tasks": (tasks_undo_and_presets, {}),
    "find": (find_notes, {}),
}
