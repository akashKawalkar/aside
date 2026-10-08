#capture/server.py
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from dataclasses import asdict
from datetime import date, datetime, time, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from config import load_config
from capture.browser_reader import BrowserEvent, to_event
from capture.monitoring import MonitoringFlag
from knowledge.notes import capture_note, search_notes_combined
from capture.router import route, classify
from knowledge.schedule import (
    capture_schedule,
    edit_schedule,
    remove_schedule,
)
from storage import (
    insert_heartbeat_log,
    insert_mark_wrong,
    latest_sent_compile_log_id,
    insert_monitoring_log,
    make_pool,
    read_all_diffs,
    save_note,
    search_notes,
    search,
    list_notes,
    update_note,
    delete_note,
    list_schedule_range,
    get_current_and_next,
    update_task,
    create_task,
    list_tasks,
    complete_task,
    get_pending_embeddings,
    mark_embedding_failed,
    index,
    Embedder,
    delete_task,
    list_task_log,
    drop_stale_tasks,
    insert_many,
    list_sessions_range,
    list_completed_tasks,
    count_notes,
    list_tasks_due,
    list_observations,
    restore_task,
    insert_statement,
    mark_observation_deleted,
)
from capture.context_routes import make_context_router
from capture.data_routes import make_data_router
from capture.persistent_routes import make_persistent_router
from capture.heartbeat import heartbeat_files
from review.daily import run_daily_worker
from review.generate import ReviewGenerator
from review.runner import run_review_worker
from review.wiring import make_review_generator
from review.store import DbBackend, ReviewSettings, ReviewStore
from capture import llm_run
from capture.schedule_gen_routes import make_schedule_gen_router
from extractor.worker import run_extractor_worker
from patterns.job import run_pattern_worker
from context.compile import compile_context
from context.items import Situation
from context.recipe import load_recipe
from context.sources import EmptySource
from context.sources.persistent_file import persistent_file_source
from context.sources.skills import skills_source
from context.sources.notes import notes_source
from context.sources.current_session import CurrentSessionSource
from context.sources.schedule import ScheduleSource
from context.sources.tasks import TasksSource
from context.sources.observations import observations_source
from llm.approved import QuotaExceeded
from llm.client import Message, load_profile
from storage import insert_compile_log
import logging
logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------

class OperationResponse(BaseModel):
    status: Literal["ok","ambiguous","error"]
    message: str | None = None
    detail: str | None = None
    data: Any | None = None
class ScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    start_at: datetime | None = None
    end_at: datetime | None = None


class ScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    start_at: datetime
    end_at: datetime
class InputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=10_000)
    mode: str
    key: str
class MonitoringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ReviewSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_enabled: bool
    frequency: Literal["daily", "weekly", "monthly"]
    recipient: str = Field(default="", max_length=254)
    time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    body: str = Field(min_length=1)
    use_when: str = ""                                  # comma-separated trigger words
    affects: str = Field(default="", max_length=200)


class SkillUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    body: str | None = Field(default=None, min_length=1)
    use_when: str | None = None
    affects: str | None = Field(default=None, max_length=200)
    usage_count: int | None = Field(default=None, ge=0)
    correction_count: int | None = Field(default=None, ge=0)


class NoteUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=10_000)
    due_at: datetime | None = None
class TaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=10_000)
    due_at: datetime | None = None
def op_ok(message: str = "", data: Any | None = None) -> OperationResponse:
    return OperationResponse(status="ok", message=message, data=data)


def op_error(message: str, data: Any | None = None,*, detail: str | None = None) -> OperationResponse:
    return OperationResponse(status="error", message=message, detail=detail, data=data)


# ---------------------------------------------------------------------------
# Handler contracts
#
# server.py does not know who implements these.
# Future chunks provide handlers when the application is wired together.
# ---------------------------------------------------------------------------

# Settings-page handlers. Each defaults to a Postgres-backed implementation
# below, but can be overridden the same way capture/chat handlers are,
# so tests can swap in a fake without touching this file.
TableHandler = Callable[[str], Awaitable[OperationResponse]]


# ---------------------------------------------------------------------------
# Default settings-page handlers
#
# These talk to storage/compiler directly rather than being injected,
# since they're simple reads/writes against chunk B, not business logic
# owned by a not-yet-built chunk (unlike capture/chat above). They can
# still be overridden via create_app(...) the same way.
# ---------------------------------------------------------------------------

# Only tables the settings page is allowed to browse — never resolve an
# arbitrary table name straight from the URL into SQL.
BROWSABLE_TABLES = {
    "events",
    "sessions",
    "notes",
    "agent_runs",
    "eval_runs",
    "persistent_entry",
    "diff_log",
    "compile_log",
    "llm_trace",
    "schedule_log",
    "schedule_snapshot",
    "task_due_log",
    "monitoring_log",
    "heartbeat_log",
    "day_record",
    "mark_wrong_log",
    "tasks",
    "task_log",
    "schedule",
    "schedule_draft",
    "observations",
}

async def _default_diff_log_handler() -> OperationResponse:
    pool = app.state.db_pool
    entries = await read_all_diffs(pool)

    return op_ok(
        message=f"Loaded {len(entries)} diff log entries.",
        data={"entries": entries},
    )

async def _default_table_handler(name: str) -> OperationResponse:
    if name not in BROWSABLE_TABLES:
        return op_error(f"'{name}' is not a browsable table.")

    pool = app.state.db_pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT * FROM {name} ORDER BY id DESC LIMIT 200"
            )

            columns = [desc.name for desc in cur.description]
            rows = await cur.fetchall()

    def json_safe(value):
        if hasattr(value, "to_list"):
            return value.to_list()

        if isinstance(value, (datetime, date, time)):
            return value.isoformat()

        return value

    return op_ok(
        message=f"Loaded {len(rows)} rows from {name}.",
        data={
            "columns": columns,
            "rows": [
                {
                    column: json_safe(value)
                    for column, value in zip(columns, row)
                }
                for row in rows
            ],
        },
    )


async def embed_pending_notes(pool, embedder: Embedder, batch_size: int = 16) -> int:
    notes = await get_pending_embeddings(pool, batch_size)

    if not notes:
        return 0

    indexed = 0

    for note in notes:
        try:
            ok = await index(
                pool,
                table="notes",
                record_id=note["id"],
                content=note["text"],
                embedder=embedder,
            )

            if ok:
                indexed += 1
            else:
                logger.info(
                    "note changed while embedding; discarding stale vector id=%s",
                    note["id"],
                )

        except Exception:
            logger.exception(
                "failed to index note embedding id=%s",
                note["id"],
            )
            await mark_embedding_failed(pool, note["id"])

    return indexed
def create_app(
    *,
    table_handler: TableHandler | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """
    Create the local HTTP service.

    The service owns:
      - HTTP routes
      - request validation
      - response formatting
      - CORS policy

    It does not own:
      - persistence
      - business logic
      - agent logic
      - sessionization
      - classification
      - memory
    """
    review_store = ReviewStore(DbBackend(lambda: app.state.db_pool))

    async def _run_note_embedding_worker(
        pool,
        embedder: Embedder,
        interval: float = 5.0,
    ) -> None:
        while True:
            try:
                embedded = await embed_pending_notes(pool, embedder)

                if embedded:
                    logger.info("embedded %d notes", embedded)

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("note embedding worker failed")

            await asyncio.sleep(interval)
    async def _run_task_expiry_worker(
        pool,
        interval: float = 600.0,
    ) -> None:
        """Drop stale tasks (over 4 days overdue or postponed twice), logging each as 'dropped' with its reason."""
        while True:
            try:
                dropped = await drop_stale_tasks(pool)

                if dropped:
                    logger.info("dropped %d stale tasks", len(dropped))

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("task expiry worker failed")

            await asyncio.sleep(interval)

    async def _run_heartbeat_sampler(pool, interval: float, stale_after: float) -> None:
        """Record every `interval` seconds whether the collector and ingestor are alive, so a gap can be explained later."""
        collector, ingestor = heartbeat_files(load_config().spool_dir.parent)
        while True:
            try:
                await insert_heartbeat_log(
                    pool, collector_ok=collector.alive(stale_after), ingestor_ok=ingestor.alive(stale_after)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("heartbeat sampler failed")

            await asyncio.sleep(interval)

    _make_review_generator = make_review_generator

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        pool = make_pool()
        await pool.open()
        app.state.db_pool = pool
        await review_store.import_legacy_files(load_config().spool_dir.parent)
        app.state.review_generator = _make_review_generator(pool)
        embedder = Embedder()
        await asyncio.to_thread(embedder.warm)
        embedding_task = asyncio.create_task(_run_note_embedding_worker(pool, embedder))
        expiry_task = asyncio.create_task(_run_task_expiry_worker(pool))
        in_action = load_config().cloud.nightly_in_action     # the nightly Action owns review, daily record, patterns, extractor
        review_task = None if in_action else asyncio.create_task(
            run_review_worker(app.state.review_generator, review_store)
        )
        dq = load_config().data_quality
        heartbeat_task = asyncio.create_task(
            _run_heartbeat_sampler(pool, dq.heartbeat_sample_interval, dq.heartbeat_stale_after)
        )
        daily_task = None if in_action else asyncio.create_task(run_daily_worker(pool, app.state.review_generator, dq))
        pattern_task = None if in_action else asyncio.create_task(run_pattern_worker(pool, load_config().patterns))
        extractor_task = None if in_action else asyncio.create_task(run_extractor_worker(pool))     # idle while [llm] background_enabled = false
        try:
            yield
        finally:
            workers = [t for t in (embedding_task, expiry_task, review_task, heartbeat_task, daily_task, pattern_task, extractor_task) if t]
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            await pool.close()
    app = FastAPI(
        title="Personal Agent Local Service",
        docs_url="/docs",
        redoc_url=None,
        lifespan = lifespan
    )

    app.include_router(make_context_router(op_ok, op_error))
    app.include_router(make_data_router(op_ok, op_error))
    app.include_router(make_persistent_router(op_ok, op_error, load_config().memory))
    app.include_router(make_schedule_gen_router(op_ok, op_error))

    @app.get("/observations", response_model=OperationResponse)
    async def observations() -> OperationResponse:
        rows = await list_observations(app.state.db_pool)
        return op_ok(data={"observations": rows})

    @app.delete("/observations/{observation_id}", response_model=OperationResponse)
    async def delete_observation(observation_id: int) -> OperationResponse:
        if not await mark_observation_deleted(app.state.db_pool, observation_id):
            return op_error("Observation not found.")
        return op_ok(message="Observation deleted.")
    
    # Local development: allow the Chrome extension origin to call localhost.
    # Extension IDs aren't stable across dev/packed builds, so this stays
    # wide open rather than allowlisting one ID — acceptable because this
    # service only ever binds to localhost (pure local trust, per design).
    origins = cors_origins or ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS","PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    table_fn = table_handler or _default_table_handler
    config = load_config()
    # ttl=0: always read the file, so a change made by another process shows at once.
    monitoring = MonitoringFlag(config.monitoring_file, ttl=0)
    diff_log_fn = _default_diff_log_handler
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/monitoring", response_model=OperationResponse)
    async def get_monitoring() -> OperationResponse:
        return op_ok(data={"enabled": monitoring.enabled()})

    @app.get("/privacy", response_model=OperationResponse)
    async def get_privacy() -> OperationResponse:
        """What may leave the machine (plan §3.9). Read-only: the rules live in config.toml [privacy] and [llm]."""
        cfg = load_config()
        return op_ok(data={
            "deny_sources": cfg.privacy.deny_sources, "deny_tags": cfg.privacy.deny_tags,
            "background_enabled": cfg.llm.background_enabled, "daily_call_cap": cfg.llm.daily_call_cap,
            "calls_today": await llm_run.calls_today(app.state.db_pool),
        })

    @app.post("/monitoring", response_model=OperationResponse)
    async def set_monitoring(request: MonitoringRequest) -> OperationResponse:
        # The collector reads this file, so pausing works even when the server is down.
        changed = monitoring.enabled() != request.enabled
        monitoring.set(request.enabled)
        if changed:
            await insert_monitoring_log(app.state.db_pool, enabled=request.enabled)
        logger.info("monitoring %s", "resumed" if request.enabled else "paused")
        return op_ok(
            message="Monitoring resumed." if request.enabled else "Monitoring paused.",
            data={"enabled": request.enabled},
        )

    @app.post("/events/browser", response_model=OperationResponse)
    async def browser_event(request: BrowserEvent) -> OperationResponse:
        if not monitoring.enabled():
            return op_ok(message="Monitoring is paused.", data={"recorded": False})

        event = to_event(request, config.title_blocklist_domains)
        await insert_many(app.state.db_pool, [event])
        return op_ok(data={"recorded": True})

    @app.get("/tables/{name}", response_model=OperationResponse)
    async def table(name: str) -> OperationResponse:
        result = table_fn(name)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def find_notes(q: str, limit: int) -> list[dict]:
        """Local note lookup: keyword (any word of 3+ letters) plus semantic similarity from the local embedder.
        No model API is involved and nothing is written."""
        pool = app.state.db_pool
        results = await search_notes_combined(
            q,
            search_notes_repo=lambda query, search_limit: search_notes(pool, query, limit=search_limit, match_any=True),
            semantic_search=lambda **kwargs: search(pool, **{**kwargs, "min_similarity": load_config().memory.note_similarity_cutoff}),
            limit=limit,
        )
        return [{"id": r.id, "text": r.text, "tags": list(r.tags), "source": r.source,
                 "created_at": r.created_at, "match": r.match} for r in results]

    @app.get("/notes/search", response_model=OperationResponse)
    async def notes_search(q: str, limit: int = 10) -> OperationResponse:
        if not q.strip():
            return op_error("Search query must not be empty.")

        if limit <= 0:
            return op_error("limit must be greater than zero.")

        results = await find_notes(q, limit)
        return op_ok(message=f"Found {len(results)} notes.", data={"results": results})

    @app.get("/notes", response_model=OperationResponse)
    async def get_notes_route(limit: int = 50) -> OperationResponse:
        pool = app.state.db_pool
        notes = await list_notes(pool, limit=limit)
        return op_ok(
            message=f"Loaded {len(notes)} notes.",
            data={"notes": notes},
        )

    @app.patch("/notes/{note_id}", response_model=OperationResponse)
    async def update_note_route(
        note_id: int,
        request: NoteUpdateRequest,
    ) -> OperationResponse:
        pool = app.state.db_pool
        note = await update_note(
            pool,
            note_id,
            text=request.text,
            tags=request.tags,
        )
        if note is None:
            return op_error("Note not found.")
        return op_ok(message="Note updated.", data={"note": note})

    @app.delete("/notes/{note_id}", response_model=OperationResponse)
    async def delete_note_route(note_id: int) -> OperationResponse:
        pool = app.state.db_pool
        deleted = await delete_note(pool, note_id)
        if not deleted:
            return op_error("Note not found.")
        return op_ok(message="Note deleted.")

    @app.get("/settings/diff-log", response_model=OperationResponse)
    async def diff_log() -> OperationResponse:
        return await diff_log_fn()
    @app.post("/input", response_model=OperationResponse)
    async def input_route(request: InputRequest) -> OperationResponse:
        result = route(request.text, request.mode, request.key)

        if result.error is not None:
            return op_error(result.error)

        pool = app.state.db_pool
        ring = "green"
        fallback = False

        # "wrong:" marks the compile behind the last call that was actually SENT (a compile is logged when staged, so the
        # newest compile_log row may be one the user rejected); the reason is optional.
        if result.destination == "wrong":
            mark_id = await insert_mark_wrong(
                pool, target="reply", compile_log_id=await latest_sent_compile_log_id(pool), reason=result.text or None
            )
            logger.info("input routed destination=wrong id=%s", mark_id)
            return op_ok(data={"destination": "wrong", "saved_as": "wrong", "id": mark_id, "ring": ring,
                               "fallback": False, "reason": result.reason})

        if result.destination == "find":
            found = await find_notes(result.text, 10)
            logger.info("input routed destination=find results=%d", len(found))
            return op_ok(data={"destination": "find", "saved_as": "none", "ring": ring, "fallback": False,
                               "reason": result.reason, "notes": found})

        # Chat routing
        target = result.destination
        if target == "chat_script":
            fallback, ring = True, "yellow"
            target = classify(result.text)
        elif target == "chat_llm":
            # Compile context with the chat recipe and ask the model directly (the daily cap and the privacy layer are the guards)
            profile = getattr(app.state, "llm_profile", None) or load_profile()
            recipe = load_recipe("chat")
            sources = {
                "current_session": CurrentSessionSource(),
                "persistent_file": persistent_file_source(pool),
                "skills": skills_source(pool),
                "schedule": ScheduleSource(lambda **kw: list_schedule_range(pool, **kw)),
                "tasks": TasksSource(lambda **kw: list_tasks(pool, **kw)),
                "notes": notes_source(pool),
                "observations": observations_source(pool),
                "history": EmptySource("history"),
            }
            async def _safe_save_log(row):
                try:
                    return await insert_compile_log(pool, row)
                except Exception:
                    logger.warning("Could not save compile log, proceeding without log_id")
                    return None

            compiled = await compile_context(
                Situation("chat", query=result.text, extra={"is_user_message": True}),
                recipe,
                sources,
                profile,
                save_log=_safe_save_log,
                max_item_tokens=load_config().llm.max_item_tokens,
            )
            messages = [
                Message("system", compiled.text),
                Message("user", result.text),
            ]
            try:
                completion = await llm_run.run_user_call(
                    app, profile=profile, messages=messages, operation="chat", compile_log_id=compiled.id,
                )
            except QuotaExceeded as exc:
                return op_error(str(exc))
            except Exception as exc:
                logger.exception("chat model call failed")
                return op_error(f"The model call failed: {exc}")
            return op_ok(
                message="Reply ready.",
                data={"destination": "chat_llm", "saved_as": "none", "ring": "yellow", **llm_run.reply_data(completion, compiled.id)},
            )

        if target == "note" or target == "journal_note":
            note = await capture_note(
                result.text,
                save_note=lambda payload: save_note(pool, payload),
                tags=["journal"] if target == "journal_note" else [],
                source="panel",
            )

            if note is None:
                return op_error("Note capture failed.")

            record_id = note.id
            try:        # raw material for the nightly extractor; a failure here must never lose the note itself
                await insert_statement(pool, result.text, "journal" if target == "journal_note" else "note")
            except Exception:
                logger.warning("could not record statement for note %s", record_id)

        elif target == "task":
            task = await create_task(pool, text=result.text)
            record_id = task["id"]

            # An empty title is saved, but flagged.
            if not task["text"]:
                ring = "yellow"

        elif target == "schedule":
            try:
                entry = await capture_schedule(
                    pool,
                    title=result.text,
                    now=datetime.now(timezone.utc),
                )
            except ValueError as exc:
                return op_error(str(exc))

            record_id = entry["id"]

        else:
            return op_error("Unsupported input destination.")

        logger.info(
            "input routed destination=%s saved_as=%s id=%s reason=%s",
            result.destination,
            target,
            record_id,
            result.reason,
        )

        return op_ok(
            data={
                "destination": result.destination,
                "saved_as": target,
                "id": record_id,
                "ring": ring,
                "fallback": fallback,
                "reason": result.reason,
            },
        )

    @app.post("/schedule", response_model=OperationResponse)
    async def create_schedule_route(
        request: ScheduleCreateRequest,
    ) -> OperationResponse:
        try:
            entry = await capture_schedule(
                app.state.db_pool,
                title=request.title,
                now=datetime.now(timezone.utc),
                start_at=request.start_at,
                end_at=request.end_at,
            )
        except ValueError as exc:
            return op_error(str(exc))

        return op_ok(
            message="Schedule entry created.",
            data=entry,
        )

    @app.get("/schedule/next", response_model=OperationResponse)
    async def schedule_next() -> OperationResponse:
        result = await get_current_and_next(
            app.state.db_pool,
            datetime.now(timezone.utc),
        )

        return op_ok(
            message="Loaded current and next schedule.",
            data=result,
        )

    @app.get("/schedule", response_model=OperationResponse)
    async def schedule_range(
        date: date | None = None,
    ) -> OperationResponse:
        if date is None:
            date = datetime.now().date()

        start_at = datetime.combine(
            date,
            time.min,
        ).astimezone()

        end_at = datetime.combine(
            date,
            time.max,
        ).astimezone()

        entries = await list_schedule_range(
            app.state.db_pool,
            start=start_at,
            end=end_at,
        )

        return op_ok(
            message=f"Loaded {len(entries)} schedule entries.",
            data={"entries": entries},
        )

    @app.patch("/schedule/{schedule_id}", response_model=OperationResponse)
    async def update_schedule_route(
        schedule_id: int,
        request: ScheduleUpdateRequest,
    ) -> OperationResponse:
        try:
            entry = await edit_schedule(
                app.state.db_pool,
                schedule_id,
                title=request.title,
                start_at=request.start_at,
                end_at=request.end_at,
            )
        except ValueError as exc:
            return op_error(str(exc))

        return op_ok(
            message="Schedule entry updated.",
            data=entry,
        )
    @app.post("/tasks", response_model=OperationResponse)
    async def create_task_route(
        request: TaskCreateRequest,
    ) -> OperationResponse:
        try:
            task = await create_task(
                app.state.db_pool,
                text=request.text,
                due_at=request.due_at,
            )
        except ValueError as exc:
            return op_error(str(exc))

        return op_ok(
            message="Task created.",
            data=task,
        )


    @app.delete("/tasks/{task_id}", response_model=OperationResponse)
    async def delete_task_route(task_id: int) -> OperationResponse:
        deleted = await delete_task(
            app.state.db_pool,
            task_id=task_id,
        )

        if not deleted:
            return op_error("Task not found.")

        return op_ok(
            message="Task deleted.",
        )
    @app.patch("/tasks/{task_id}", response_model=OperationResponse)
    async def update_task_route(
    task_id: int,
    request: TaskUpdateRequest,
) -> OperationResponse:
        if not request.model_fields_set:
            return op_error("At least one task field must be provided.")
        try:
            update_kwargs = {
                "task_id": task_id,
                "text": request.text,
            }

            if "due_at" in request.model_fields_set:
                update_kwargs["due_at"] = request.due_at

            task = await update_task(
                app.state.db_pool,
                **update_kwargs,
            )
        except ValueError as exc:
            return op_error(str(exc))

        if task is None:
            return op_error("Task not found.")

        return op_ok(
            message="Task updated.",
            data=task,
        )
    @app.post("/tasks/{task_id}/complete", response_model=OperationResponse)
    async def complete_task_route(task_id: int) -> OperationResponse:
        pool = app.state.db_pool

        task = await complete_task(
            pool,
            task_id=task_id,
        )

        if task is None:
            return op_error("Task not found or already completed.")

        return op_ok(
            data={
                "task": task,
            },
        )
    @app.post("/tasks/{task_id}/restore", response_model=OperationResponse)
    async def restore_task_route(task_id: int) -> OperationResponse:
        task = await restore_task(app.state.db_pool, task_id=task_id)
        if task is None:
            return op_error("Task not found or not completed.")
        return op_ok(message="Task restored.", data={"task": task})

    @app.get("/tasks", response_model=OperationResponse)
    async def get_tasks(limit: int = 5) -> OperationResponse:
        # The side panel asks for 5; the settings page asks for many.
        if limit < 1 or limit > 500:
            return op_error("limit must be between 1 and 500.")

        pool = app.state.db_pool

        tasks = await list_tasks(
            pool,
            status="pending",
        )

        return op_ok(
            data={
                "items": tasks[:limit],
            },
        )

    @app.get("/tasks/history", response_model=OperationResponse)
    async def get_task_history(limit: int = 200) -> OperationResponse:
        if limit < 1 or limit > 1000:
            return op_error("limit must be between 1 and 1000.")

        events = await list_task_log(app.state.db_pool, limit=limit)

        return op_ok(data={"items": events})
    @app.delete("/schedule/{schedule_id}", response_model=OperationResponse)
    async def delete_schedule_route(
        schedule_id: int,
    ) -> OperationResponse:
        deleted = await remove_schedule(
            app.state.db_pool,
            schedule_id,
        )

        if not deleted:
            return op_error("Schedule entry not found.")

        return op_ok(
            message="Schedule entry deleted.",
        )
    @app.get("/review", response_model=OperationResponse)
    async def get_review() -> OperationResponse:
        """The latest nightly review, or null if none has been written yet."""
        return op_ok(
            data={
                "review": await review_store.latest(),
                "settings": asdict(await review_store.settings()),
            },
        )

    @app.post("/review/preview", response_model=OperationResponse)
    async def preview_review() -> OperationResponse:
        """Today's review as it stands now. Nothing is saved or emailed."""
        review = await app.state.review_generator.generate()

        return op_ok(
            data={"date": review.review_date.isoformat(), "data": review.data, "text": review.text},
        )

    @app.post("/settings/review", response_model=OperationResponse)
    async def set_review_settings(request: ReviewSettingsRequest) -> OperationResponse:
        try:
            saved = await review_store.save_settings(ReviewSettings(**request.model_dump()))
        except ValueError as exc:
            return op_error(str(exc))

        return op_ok(data=asdict(saved))

    # Skills routes (request models are module-level: FastAPI cannot resolve annotations of classes local to this function)
    from storage.repo.skills import list_skills, create_skill, update_skill, delete_skill
    from storage.repo.statements import list_statements, delete_statement, list_candidate_items, delete_candidate_item, list_instruction_records, delete_instruction_record

    @app.get("/skills", response_model=OperationResponse)
    async def get_skills_route():
        skills = await list_skills(app.state.db_pool)
        return op_ok(data={"skills": skills})

    @app.post("/skills", response_model=OperationResponse)
    async def post_skills_route(request: SkillCreateRequest):
        skill = await create_skill(app.state.db_pool, **request.model_dump())
        return op_ok(data={"skill": skill})

    @app.patch("/skills/{skill_id}", response_model=OperationResponse)
    async def patch_skills_route(skill_id: int, request: SkillUpdateRequest):
        skill = await update_skill(app.state.db_pool, skill_id, **request.model_dump(exclude_unset=True))
        if not skill: return op_error("Not found")
        return op_ok(data={"skill": skill})

    @app.delete("/skills/{skill_id}", response_model=OperationResponse)
    async def delete_skills_route(skill_id: int):
        ok = await delete_skill(app.state.db_pool, skill_id)
        if not ok: return op_error("Not found")
        return op_ok(message="Deleted")

    # Statements & Candidates routes
    @app.get("/statements", response_model=OperationResponse)
    async def get_statements_route():
        items = await list_statements(app.state.db_pool)
        return op_ok(data={"items": items})

    @app.delete("/statements/{item_id}", response_model=OperationResponse)
    async def del_statement_route(item_id: int):
        ok = await delete_statement(app.state.db_pool, item_id)
        if not ok: return op_error("Not found")
        return op_ok(message="Deleted")

    @app.get("/candidate_items", response_model=OperationResponse)
    async def get_candidates_route():
        items = await list_candidate_items(app.state.db_pool)
        return op_ok(data={"items": items})

    @app.delete("/candidate_items/{item_id}", response_model=OperationResponse)
    async def del_candidate_route(item_id: int):
        ok = await delete_candidate_item(app.state.db_pool, item_id)
        if not ok: return op_error("Not found")
        return op_ok(message="Deleted")

    @app.get("/instruction_records", response_model=OperationResponse)
    async def get_instructions_route():
        items = await list_instruction_records(app.state.db_pool)
        return op_ok(data={"items": items})

    @app.delete("/instruction_records/{item_id}", response_model=OperationResponse)
    async def del_instruction_route(item_id: int):
        ok = await delete_instruction_record(app.state.db_pool, item_id)
        if not ok: return op_error("Not found")
        return op_ok(message="Deleted")

    return app
app = create_app()
   
