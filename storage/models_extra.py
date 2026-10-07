# storage/models_extra.py
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from storage.models import Base


class PersistentEntryRow(Base):
    """One fact in the persistent file. The six schema fields (context/persistent_schema.py) plus bookkeeping."""
    __tablename__ = "persistent_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False, server_default="user")
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_confirmed: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="active")
    retired_reason: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    replaces_id: Mapped[Optional[int]] = mapped_column(ForeignKey("persistent_entry.id", ondelete="SET NULL"), nullable=True)
    seen_days: Mapped[list] = mapped_column(ARRAY(Date), nullable=False, server_default="{}")


class DiffLogRow(Base):
    __tablename__ = "diff_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    before: Mapped[dict] = mapped_column(JSONB, nullable=False)
    after: Mapped[dict] = mapped_column(JSONB, nullable=False)
    removed: Mapped[dict] = mapped_column(JSONB, nullable=False)
    added: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
class TaskLogRow(Base):
    __tablename__ = "task_log"

    id: Mapped[int] = mapped_column(primary_key=True)

    task_id: Mapped[int] = mapped_column(nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CompileLogRow(Base):
    __tablename__ = "compile_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    situation: Mapped[str] = mapped_column(String(50), nullable=False)
    recipe_name: Mapped[str] = mapped_column(String(50), nullable=False)
    recipe_hash: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    offered: Mapped[list] = mapped_column(JSONB, nullable=False)
    chosen: Mapped[list] = mapped_column(JSONB, nullable=False)
    dropped: Mapped[list] = mapped_column(JSONB, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class LlmTraceRow(Base):
    __tablename__ = "llm_trace"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    finish_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    compile_log_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True
    )
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ScheduleLogRow(Base):
    __tablename__ = "schedule_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    before: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ScheduleSnapshotRow(Base):
    __tablename__ = "schedule_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    entries: Mapped[list] = mapped_column(JSONB, nullable=False)
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskDueLogRow(Base):
    __tablename__ = "task_due_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    old_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    new_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MonitoringLogRow(Base):
    __tablename__ = "monitoring_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class HeartbeatLogRow(Base):
    __tablename__ = "heartbeat_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    collector_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ingestor_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)


class DayRecordRow(Base):
    __tablename__ = "day_record"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    late: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MarkWrongLogRow(Base):
    __tablename__ = "mark_wrong_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    target: Mapped[str] = mapped_column(Text, nullable=False)
    compile_log_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class SkillRow(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    use_when: Mapped[str] = mapped_column(Text, nullable=False, server_default="")        # comma-separated trigger words
    affects: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    correction_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StatementRow(Base):
    __tablename__ = "statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CandidateItemRow(Base):
    __tablename__ = "candidate_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    effect: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InstructionRecordRow(Base):
    __tablename__ = "instruction_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleDraftRow(Base):
    """One version of a generated schedule for one day (migration add_schedule_draft). `proposed` is never edited."""
    __tablename__ = "schedule_draft"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schedule_draft.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="open")   # open | superseded | accepted | discarded
    source: Mapped[str] = mapped_column(String(12), nullable=False)                          # llm | placeholder
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    instruction: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    proposed: Mapped[list] = mapped_column(JSONB, nullable=False)    # the entries exactly as generated; never touched afterwards
    entries: Mapped[list] = mapped_column(JSONB, nullable=False)     # the working copy the user edits and accepts
    rejected: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    compile_log_id: Mapped[Optional[int]] = mapped_column(ForeignKey("compile_log.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
