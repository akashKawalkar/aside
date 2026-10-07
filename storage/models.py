# storage/models.py
from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func, Index, CheckConstraint, BigInteger, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB

class Base(DeclarativeBase):
    pass


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # "app" or "idle". Required for every real session by the NOT VALID
    # check ck_sessions_complete; the columns below are nullable at the
    # column level only so that the one pre-existing placeholder row is
    # tolerated (see alembic/versions/add_session_fields.py).
    kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    app: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Sum of fragment durations. Wall-clock time is ended_at - started_at.
    active_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    event_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Identity key: one session per first event, so re-sessionizing is idempotent.
    first_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id"), unique=True, nullable=True
    )

    events: Mapped[list["Event"]] = relationship(
        back_populates="session",
        foreign_keys="Event.session_id",
    )



class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_ts_start", "ts_start"),
        Index("ix_events_source_ts_start", "source", "ts_start"),
        CheckConstraint("ts_end >= ts_start", name="ck_events_ts_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    app: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    window_title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    ts_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    # Membership: the session this event was folded into, if any.
    session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    session: Mapped[Optional["Session"]] = relationship(
        back_populates="events",
        foreign_keys=[session_id],
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )