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
    """Ask for a draft, read the prompt, approve, accept / edit / revise, see it in settings, accept the rest."""
    panel = settings = None
    try:
        panel = await b.page(urls["panel"], 420, 900)
        await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the Draft tomorrow link")
        assert await panel.text("#tile-schedule-draft .draft-link") == ["Draft tomorrow"]
        await panel.shot(shots / "1_link.png"); ok("the link is shown when tomorrow is empty")

        await panel.click("#tile-schedule-draft .draft-link")
        await panel.type(".draft-form input", "zz keep the evening quiet")
        await panel.click(".draft-form .btn", "Prepare draft")
        await panel.wait("document.querySelector('.approval-card')", what="the approval card")
        prompt = (await panel.text(".approval-context-pre"))[0]
        assert "[system]" in prompt and "[user]" in prompt and "zz keep the evening quiet" in prompt and "07:00-08:00 zz Gym" in prompt, prompt[:300]
        await panel.js("document.querySelector('.approval-context-details').open = true")
        await panel.shot(shots / "3_approval.png"); ok("the approval card shows the exact system + user prompt; nothing is sent yet")

        await panel.click(".approval-btn.approve")
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
        await panel.click(".draft-form .btn", "Prepare revision")
        await panel.wait("document.querySelector('.approval-card')", what="revision approval")
        assert "Revise the schedule" in (await panel.text(".approval-context-pre"))[0]
        await panel.click(".approval-btn.approve")
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
        no_js_errors(panel, settings)
    finally:
        pass


async def chat_card(b, urls, shots):
    """The chat approval card (now the shared component): stage, approve, cancel."""
    panel = await b.page(urls["panel"], 420, 900)
    await panel.wait("document.getElementById('composer-input')", what="the composer")
    await panel.type("#composer-input", "zz what is on my plate")
    await panel.wait("!document.getElementById('send-button').disabled", what="send enabled")
    await panel.click("#send-button")
    await panel.wait("document.querySelector('#chat-thread .approval-card')", what="the chat approval card")
    meta = (await panel.text("#chat-thread .approval-meta"))[0]
    prompt = (await panel.text("#chat-thread .approval-context-pre"))[0]
    assert meta.startswith("Model:") and "[system]" in prompt and "[user]" in prompt and "zz what is on my plate" in prompt, (meta, prompt[:200])
    ok("a chat message stages a card showing both messages")

    await panel.click("#chat-thread .approval-btn.approve")
    await panel.wait("document.querySelector('#chat-thread .approval-card')?.textContent.includes('Approved and executed')", what="approved text")
    await panel.shot(shots / "10_chat_approved.png"); ok("approving runs the call and shows the reply in the thread")

    await panel.type("#composer-input", "zz something private")
    await panel.click("#send-button")
    await panel.wait("document.querySelectorAll('#chat-thread .approval-card').length === 2", what="second card")
    await panel.click("#chat-thread .chat-row:last-child .approval-btn.reject")
    await panel.wait("[...document.querySelectorAll('#chat-thread .approval-card')].pop().textContent.includes('cancelled')", what="cancelled")
    pending = await panel.js(f"fetch('{urls['api']}/llm/pending').then(r => r.json()).then(j => j.data.items.length)")
    assert pending == 0, pending
    ok("cancelling discards the call and nothing is left pending")
    no_js_errors(panel)


async def rule_based_and_failure(b, urls, shots):
    """Needs the server started with UI_BAD=1: the rule-based draft (no model), discard, and an unusable reply."""
    panel = await b.page(urls["panel"], 420, 900)
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link")
    await panel.click("#tile-schedule-draft .draft-link")
    await panel.click(".draft-form .btn", "Rule-based draft")
    await panel.wait("document.querySelectorAll('.draft-list .draft-entry').length === 2", what="copied entries")
    assert await panel.text(".draft-entry .event-title") == ["zz Gym", "zz Deep work"]
    assert await panel.js("!document.querySelector('.approval-card')")
    assert (await panel.text(".draft-head"))[0].endswith("rule-based")
    await panel.shot(shots / "11_rulebased.png"); ok("the rule-based draft copies last Tuesday with no model call")

    await panel.click(".draft-actions .btn", "Discard draft")
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link again")
    ok("discarding returns to the quiet link")

    await panel.click("#tile-schedule-draft .draft-link")
    await panel.click(".draft-form .btn", "Prepare draft")
    await panel.wait("document.querySelector('.approval-card')", what="approval")
    await panel.click(".approval-btn.approve")
    await panel.wait("document.querySelector('.approval-card')?.textContent.includes('could not be used')", what="failure text")
    await panel.shot(shots / "12_unusable.png")
    await panel.click(".approval-card .btn", "Back")
    await panel.wait("document.querySelector('#tile-schedule-draft .draft-link')", what="the link after a failure")
    assert await panel.js(f"fetch('{urls['api']}/schedule/draft/status').then(r => r.json()).then(j => j.data.draft === null)")
    ok("an unusable reply gives a plain message and creates no draft")
    no_js_errors(panel)


TABS = ["general", "tasks", "task-history", "schedule", "drafts", "review", "inspect", "context", "data-gaps",
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


# name -> (function, environment for the test server)
SCENARIOS = {
    "main": (main_flow, {}),
    "chat": (chat_card, {}),
    "edge": (rule_based_and_failure, {"UI_BAD": "1"}),
    "tabs": (settings_tabs, {}),
}
