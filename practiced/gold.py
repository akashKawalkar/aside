"""The worked example for the digest prompt: a day (shaped like Nov 12, abridged) and the answer to give for it.

It is built from the same fields the real builder produces, so the prompt shows the model exactly what it will be given.
A test checks that GOLD_OUTPUT passes the digest validator for GOLD_DAY.
"""
from __future__ import annotations


def _a(track, start, end, category, minutes, *labels, late=False):
    return {"track": track, "start": start, "end": end, "category": category, "minutes": minutes,
            "labels": [{"label": l, "minutes": m} for l, m in labels], "late_night": late}


GOLD_DAY = {
    "day": "2026-11-12",
    "plan": [
        {"id": 1, "title": "Morning Workout", "start": "07:30", "end": "07:50", "planned_min": 20, "source": "generated, unedited",
         "verdict": "NO SIGNAL", "on_plan_min": 0, "on_plan_pct": 0, "idle_min": 0, "phone_min": 0, "activities": []},
        {"id": 2, "title": "Daily Meditation Routine", "start": "08:00", "end": "09:00", "planned_min": 60, "source": "generated, unedited",
         "verdict": "NO SIGNAL", "on_plan_min": 0, "on_plan_pct": 0, "idle_min": 0, "phone_min": 13,
         "activities": [_a("phone", "08:40", "09:00", "media", 13, ("YouTube (phone)", 13))]},
        {"id": 3, "title": "AI Engineering Study Block", "start": "10:00", "end": "13:00", "planned_min": 180, "source": "generated, unedited",
         "verdict": "OFF-PLAN", "on_plan_min": 40, "on_plan_pct": 22, "idle_min": 20, "phone_min": 90,
         "activities": [
             _a("laptop", "10:50", "11:10", "study", 14, ("Libgen: browsing books", 14)),
             _a("phone", "10:10", "11:10", "media", 37, ("YouTube (phone)", 37)),
             _a("laptop", "11:20", "11:30", "study", 5, ("PDF: Hands-On LLM Serving and Optimization", 5)),
             _a("laptop", "11:40", "11:50", "ai_assist", 5, ("ChatGPT: is prewedding shoots allowed in regional park", 5)),
             _a("laptop", "11:50", "12:10", "media", 17, ("YouTube: home/feed", 1), ("YouTube (spiritual): Must Know This Before Eating Eggs", 3), ("YouTube: Chaewon", 13)),
             _a("laptop", "12:10", "12:30", "social", 12, ("Instagram", 12)),
         ]},
        {"id": 4, "title": "AI Engineering Hands-on Practice", "start": "14:30", "end": "17:30", "planned_min": 180, "source": "generated, unedited",
         "verdict": "PARTIAL", "on_plan_min": 50, "on_plan_pct": 28, "idle_min": 67, "phone_min": 165,
         "activities": [
             _a("phone", "14:30", "17:30", "social", 165, ("Instagram (phone)", 165)),
             _a("laptop", "14:50", "15:50", "job_search", 38, ("LinkedIn search/jobs: Search", 38)),
             _a("laptop", "15:50", "16:20", "ai_assist", 25, ("ChatGPT: Explain VLLM Architecture", 5), ("ChatGPT", 16), ("ChatGPT: reply to a recruiter", 2)),
             _a("laptop", "16:40", "16:50", "ai_assist", 7, ("ChatGPT: Explain VLLM Architecture", 7)),
             _a("laptop", "17:10", "17:20", "ai_assist", 8, ("ChatGPT: Explain VLLM Architecture", 8)),
         ]},
    ],
    "outside": [
        _a("laptop", "13:20", "14:30", "media", 59, ("YouTube: home/feed", 6), ("YouTube shorts: GTA radio", 6), ("YouTube: Who is in the wrong?", 2)),
        _a("phone", "22:00", "23:20", "media", 67, ("YouTube (phone)", 67)),
        _a("laptop", "23:20", "00:00", "coding", 34, ("VS Code: linkedin-lead-finder (extension scroll)", 34), late=True),
        _a("phone", "00:00", "02:00", "media", 120, ("YouTube (phone)", 120), late=True),
    ],
    "sinks": [{"what": "phone Instagram (phone)", "minutes": 350}, {"what": "phone YouTube (phone)", "minutes": 370},
              {"what": "laptop LinkedIn search/jobs", "minutes": 160}],
    "best_stretch": {"track": "laptop", "start": "23:20", "end": "00:00", "category": "coding", "minutes": 34,
                     "label": "VS Code: linkedin-lead-finder (extension scroll)", "inside_plan": False},
    "unmapped": [{"label": "laptop: obsidian.exe", "minutes": 22}],
    "totals": {"laptop": {"idle": 277, "media": 202, "job_search": 132, "coding": 61, "ai_assist": 60, "social": 43},
               "phone": {"media": 420, "social": 350}},
    "tasks": {"done": [], "slipping": [{"text": "Update resume", "slip_count": 4, "due": "2026-11-09"}]},
    "plan_changes": {"kept_unedited": ["Morning Workout", "Daily Meditation Routine", "AI Engineering Study Block",
                                       "AI Engineering Hands-on Practice"],
                     "edited_after_accepting": [], "added_by_user": [], "discarded": [], "retimed_in_draft": []},
}

GOLD_OUTPUT = {
    "plan_blocks": [
        {"title": "Morning Workout", "note": "The devices cannot see exercise, so there is no verdict on whether it happened."},
        {"title": "Daily Meditation Routine", "note": "No verdict possible. The phone shows YouTube 08:40-09:00 inside the window."},
        {"title": "AI Engineering Study Block",
         "note": "Libgen browsing and one PDF counted; the rest was YouTube, Instagram, one unrelated ChatGPT chat and idle."},
        {"title": "AI Engineering Hands-on Practice",
         "note": "ChatGPT on VLLM architecture in three bursts with idle gaps, and LinkedIn Search; phone Instagram ran through the whole block."},
    ],
    "time_sinks": [
        {"what": "phone Instagram (phone)", "note": "165 of these minutes fell inside the 14:30 practice block."},
        {"what": "phone YouTube (phone)", "note": "Much of it after midnight and again at 22:00."},
        {"what": "laptop LinkedIn search/jobs", "note": "Unclear whether this was job search or lead-finding."},
    ],
    "best_stretch": "23:20-00:00 coding on linkedin-lead-finder (34m), outside the plan",
    "anomalies": [
        "Both planned study blocks were accepted unedited and mostly not followed",
        "Late-night: phone YouTube 00:00-02:00",
    ],
    "summary": ("The plan was accepted unedited and followed only in fragments. Real study happened in short bursts (Libgen, an "
                "LLM-serving PDF, VLLM chats), while Instagram and YouTube filled most of the day. The most focused work was 34 "
                "minutes of coding at 23:20, unplanned."),
    "title_tags": [{"label": "laptop: obsidian.exe", "category": "work_admin"}],
}
