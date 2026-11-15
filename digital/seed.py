"""The starting category rules. They are only a seed: once copied into the `category_rule` table they are edited there.

These rules were reviewed and approved by the user on 2026-11-15.
"""
from __future__ import annotations

from dataclasses import replace

from digital.rules import Rule

# --- YouTube split by title (reviewed by the user) -------------------------------------------------------------------
LEARNING = (
    "tutorial", "course", "lecture", "explained", "learn python", "learn to code", "python", "machine learning", "llm", "transformer",
    "vllm", "neural", "algorithm", "system design", "leetcode", "pytorch", "docker", "kubernetes", "rag ", "agents",
    "programming", "engineering", "interview", "mathematics", "physics", "statistics", "data science", "fastapi",
    "postgres", "sql", "react", "javascript", "typescript", "kotlin", "android", "cuda", "gpu", "inference", "fine-tun",
)
SPIRITUAL = (
    "ramayan", "रामायण", "bhagavad", "gita", "गीता", "sadhguru", "isha", "mantra", "bhajan", "kirtan", "satsang",
    "meditation", "yoga", "upanishad", "vedanta", "shiv", "शिव", "hanuman", "हनुमान", "chalisa", "aarti", "guru",
)

JOB_PORTALS = ("naukri", "indeed", "wellfound", "internshala", "glassdoor", "foundit")

_RULES: list[Rule] = [
    # ---- YouTube on the laptop ----
    Rule("media", "YouTube shorts", domain="youtube.com", title_has=("shorts",), priority=100, note="reviewed by the user 2026-11-15"),
    Rule("study", "YouTube (learning)", domain="youtube.com", title_has=LEARNING, priority=95, note="reviewed by the user 2026-11-15"),
    Rule("media", "YouTube (spiritual)", domain="youtube.com", title_has=SPIRITUAL, priority=90, note="reviewed by the user 2026-11-15"),
    Rule("media", "YouTube", domain="youtube.com", priority=50),
    # ---- LinkedIn (reviewed by the user) ----
    Rule("job_search", "LinkedIn search/jobs", domain="linkedin.com", title_has=("search", "jobs"), priority=100, note="reviewed by the user 2026-11-15"),
    Rule("social", "LinkedIn feed", domain="linkedin.com", title_has=("feed", "notifications", "post"), priority=95, note="reviewed by the user 2026-11-15"),
    Rule("networking", "LinkedIn profile/company", domain="linkedin.com", priority=50, note="reviewed by the user 2026-11-15"),
    # ---- job portals ----
    *[Rule("job_search", d.capitalize(), domain=d, priority=60) for d in JOB_PORTALS],
    # ---- AI assistants ----
    Rule("ai_assist", "ChatGPT", domain="chatgpt.com", priority=60, note="neutral until judged: not always productive"),
    Rule("ai_assist", "Claude", domain="claude.ai", priority=60),
    Rule("ai_assist", "Gemini", domain="gemini.google.com", priority=60),
    Rule("ai_assist", "NotebookLM", domain="notebook.google.com", priority=60),
    # ---- work, code, study sites ----
    Rule("work_admin", "Notion", domain="notion.", priority=60),
    Rule("coding", "Colab", domain="colab.research.google.com", priority=60),
    Rule("coding", "GitHub", domain="github.com", priority=60),
    Rule("coding", "LeetCode", domain="leetcode.com", priority=60),
    Rule("coding", "Supabase", domain="supabase.com", priority=60),
    Rule("coding", "Grafana", domain="grafana.net", priority=60),
    Rule("study", "Libgen", domain="libgen", priority=60),
    Rule("work_admin", "Google Drive", domain="drive.google.com", priority=60),
    Rule("work_admin", "Adobe Express", domain="express.adobe.com", priority=60),
    Rule("work_admin", "RenderCV", domain="rendercv.com", priority=60),
    # ---- comms ----
    Rule("comms", "Gmail", domain="mail.google.com", priority=60),
    Rule("comms", "WhatsApp Web", domain="web.whatsapp.com", priority=60),
    Rule("comms", "Meeting", domain="zoom.us", priority=60, use_title=False),
    Rule("comms", "Meeting", domain="meet.google.com", priority=60, use_title=False),
    Rule("comms", "Meeting", domain="meeting.zoho", priority=60, use_title=False),
    # ---- social, shopping ----
    Rule("social", "Instagram", domain="instagram.com", priority=60),
    Rule("social", "Facebook", domain="facebook.com", priority=60),
    Rule("social", "X", domain="x.com", priority=60),
    Rule("social", "Reddit", domain="reddit.com", priority=60),
    Rule("shopping", "Myntra", domain="myntra.com", priority=60),
    # ---- laptop apps ----
    Rule("coding", "VS Code", app="code.exe", source="laptop", priority=50),
    Rule("coding", "Terminal", app="windowsterminal.exe", source="laptop", priority=50, use_title=False),
    Rule("coding", "Terminal", app="powershell.exe", source="laptop", priority=50, use_title=False),
    Rule("coding", "Android Studio", app="studio64.exe", source="laptop", priority=50, use_title=False),
    Rule("study", "PDF", app="acrobat.exe", source="laptop", priority=50),
    Rule("media", "VLC", app="vlc.exe", source="laptop", priority=50),
    Rule("work_admin", "Notepad", app="notepad.exe", source="laptop", priority=50, use_title=False),
    Rule("comms", "Outlook", app="olk.exe", source="laptop", priority=50, use_title=False),
    Rule("other", "Photos", app="photos.exe", source="laptop", priority=50, use_title=False),
    Rule("other", "qBittorrent", app="qbittorrent.exe", source="laptop", priority=50, use_title=False),
    *[Rule("system", a, app=a, source="laptop", priority=45, use_title=False) for a in (
        "explorer.exe", "ShellHost.exe", "flux.exe", "nearby_share.exe", "ApplicationFrameHost.exe", "LockApp.exe",
        "voicemeeter.exe", "SearchHost.exe", "PickerHost.exe", "ShellExperienceHost.exe", "CredentialUIBroker.exe",
        "CodeSetup", "hwinfo", "StartMenuExperienceHost.exe", "TextInputHost.exe", "dwm.exe", "taskmgr.exe",
    )],
    Rule("other", "Chrome", app="chrome.exe", source="laptop", priority=10, use_title=False, note="a website the rules do not know"),
    Rule("other", "Edge", app="msedge.exe", source="laptop", priority=10, use_title=False),
    # ---- phone apps (package names; no titles on the phone) ----
    Rule("media", "YouTube (phone)", app="com.google.android.youtube", source="phone", priority=60, use_title=False),
    Rule("media", "Music", app="youtube.music", source="phone", priority=60, use_title=False),
    Rule("media", "Music", app="maxmpz.audioplayer", source="phone", priority=60, use_title=False),
    Rule("ai_assist", "ChatGPT (phone)", app="com.openai.chatgpt", source="phone", priority=60, use_title=False),
    Rule("social", "Instagram (phone)", app="com.instagram", source="phone", priority=60, use_title=False),
    Rule("social", "Facebook (phone)", app="com.facebook", source="phone", priority=60, use_title=False),
    Rule("comms", "WhatsApp", app="com.whatsapp", source="phone", priority=60, use_title=False),
    Rule("comms", "Dialer", app="dialer", source="phone", priority=60, use_title=False),
    Rule("comms", "Truecaller", app="truecaller", source="phone", priority=60, use_title=False),
    Rule("comms", "Messages", app="apps.messaging", source="phone", priority=60, use_title=False),
    Rule("comms", "Gmail (phone)", app="com.google.android.gm", source="phone", priority=60, use_title=False),
    Rule("comms", "Meet", app="apps.tachyon", source="phone", priority=60, use_title=False),
    Rule("shopping", "Myntra (phone)", app="com.myntra", source="phone", priority=60, use_title=False),
    Rule("wellbeing", "Isha Foundation", app="com.ishafoundation", source="phone", priority=60, use_title=False),
    Rule("wellbeing", "Amritvarsha", app="gurumaa.amritvarsha", source="phone", priority=60, use_title=False, note="reviewed by the user 2026-11-15"),
    Rule("study", "PDF reader (phone)", app="foobnix.pdf", source="phone", priority=60, use_title=False),
    Rule("other", "Chrome (phone)", app="com.android.chrome", source="phone", priority=10, use_title=False),
    *[Rule("system", a, app=a, source="phone", priority=45, use_title=False) for a in (
        "com.android.settings", "com.android.vending", "com.google.android.gms", "com.android.systemui",
        "com.google.android.inputmethod", "com.google.android.googlequicksearchbox", "permissioncontroller",
        "com.google.android.packageinstaller",
    )],
]

# Numbered from 1 so that id 0 always means "no rule matched".
SEED_RULES: list[Rule] = [replace(r, id=i) for i, r in enumerate(_RULES, 1)]
