# sessions/sessionizer.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta

from storage import Event


@dataclass(frozen=True)
class SessionFragment:
    """One raw event contributing to a session."""

    event: Event

    @property
    def duration(self) -> timedelta:
        return self.event.ts_end - self.event.ts_start


@dataclass(frozen=True)
class SessionCandidate:
    """
    A session candidate produced from raw events.

    This is deliberately not a database model. Persistence belongs
    to the storage layer once the final Session schema is defined.
    """

    kind: str
    app: str | None
    started_at: datetime
    ended_at: datetime
    duration: timedelta
    fragments: tuple[SessionFragment, ...]


class Sessionizer:
    """
    Turns raw laptop events into session candidates.

    No model calls, database writes, classification, or labeling happen here.
    """

    DEFAULT_ALLOWLIST = frozenset(
        {
            "chrome",
            "edge",
            "code",
            "explorer",
            "notion",
            "acrobat",
        }
    )

    MIN_EVENT_DURATION = timedelta(seconds=10)
    MIN_SESSION_DURATION = timedelta(minutes=10)
    PENDING_EXPIRY = timedelta(minutes=20)

    def __init__(
        self,
        *,
        allowlist: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.allowlist = frozenset(
            app.casefold()
            for app in (allowlist or self.DEFAULT_ALLOWLIST)
        )

    def sessionize(self, events: list[Event]) -> list[SessionCandidate]:
        """
        Convert raw events into finished session candidates.

        Events shorter than 10 seconds are discarded.

        Allowlisted apps accumulate non-contiguous active fragments.
        Once an app reaches 10 minutes cumulative usage, the accumulated
        fragments become one session.

        Pending fragments that fail to reach 10 minutes after 20 minutes
        of other active engagement are discarded.

        Idle events become independent sessions and are never merged
        with application sessions.
        """
        if not events:
            return []

        ordered = sorted(events, key=lambda event: event.ts_start)

        sessions: list[SessionCandidate] = []

        pending: dict[str, list[SessionFragment]] = {}
        pending_duration: dict[str, timedelta] = {}
        active_since_fragment: dict[str, timedelta] = {}

        for event in ordered:
            if self._duration(event) < self.MIN_EVENT_DURATION:
                continue

            # Idle is always its own session type.
            if event.kind == "idle":
                sessions.append(self._make_idle_session(event))
                continue

            if event.kind != "app_focus":
                continue

            app_key = self._app_key(event)

            # Non-allowlisted applications are represented by "other".
            if app_key not in self.allowlist:
                app_key = "other"

            fragment = SessionFragment(event=event)
            duration = fragment.duration

            # "other" is still real activity and therefore contributes
            # to expiry of pending sessions.
            self._age_pending(
                pending=pending,
                pending_duration=pending_duration,
                active_since_fragment=active_since_fragment,
                active_duration=duration,
                current_app=app_key,
            )

            pending.setdefault(app_key, []).append(fragment)
            pending_duration[app_key] = (
                pending_duration.get(app_key, timedelta())
                + duration
            )
            active_since_fragment[app_key] = timedelta()

            if pending_duration[app_key] >= self.MIN_SESSION_DURATION:
                sessions.append(
                    self._make_app_session(
                        app=app_key,
                        fragments=pending[app_key],
                    )
                )

                pending.pop(app_key, None)
                pending_duration.pop(app_key, None)
                active_since_fragment.pop(app_key, None)

        return sorted(
            sessions,
            key=lambda session: session.started_at,
        )

    def _age_pending(
        self,
        *,
        pending: dict[str, list[SessionFragment]],
        pending_duration: dict[str, timedelta],
        active_since_fragment: dict[str, timedelta],
        active_duration: timedelta,
        current_app: str,
    ) -> None:
        """
        Age pending apps using other active engagement.

        Idle time does not consume the 20-minute pending window.
        The app currently contributing activity does not age itself.
        """

        expired: list[str] = []

        for app_key in pending:
            if app_key == current_app:
                active_since_fragment[app_key] = timedelta()
                continue

            active_since_fragment[app_key] += active_duration

            if active_since_fragment[app_key] >= self.PENDING_EXPIRY:
                expired.append(app_key)

        for app_key in expired:
            pending.pop(app_key, None)
            pending_duration.pop(app_key, None)
            active_since_fragment.pop(app_key, None)

    def _make_app_session(
        self,
        *,
        app: str,
        fragments: list[SessionFragment],
    ) -> SessionCandidate:
        total_duration = sum(
            (fragment.duration for fragment in fragments),
            timedelta(),
        )

        return SessionCandidate(
            kind="app",
            app=app,
            started_at=fragments[0].event.ts_start,
            ended_at=fragments[-1].event.ts_end,
            duration=total_duration,
            fragments=tuple(fragments),
        )

    def _make_idle_session(self, event: Event) -> SessionCandidate:
        return SessionCandidate(
            kind="idle",
            app=None,
            started_at=event.ts_start,
            ended_at=event.ts_end,
            duration=event.ts_end - event.ts_start,
            fragments=(SessionFragment(event=event),),
        )

    @staticmethod
    def _duration(event: Event) -> timedelta:
        return event.ts_end - event.ts_start

    @staticmethod
    def _app_key(event: Event) -> str:
        if not event.app:
            return "other"

        # Windows reports process names such as "Code.exe"; the allowlist
        # uses the bare name ("code").
        return event.app.casefold().strip().removesuffix(".exe") or "other"