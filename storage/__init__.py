# storage/__init__.py
#
# Public surface of chunk B. Everything outside storage/ imports from here,
# never from storage.repo.* or storage.database directly.

from storage.database import make_pool
from storage.repo.event import Event, dedupe_key_for, insert_many, parse_spool_file  # see 2.2 for dedupe_key_for
from storage.embedder import Embedder, EmbeddingResult
from storage.retrieval import search, SearchResult, index, Retriever,PostgresVectorStore
from storage.repo.persistent_entries import add_persistent_entry, confirm_persistent_entry, edit_persistent_entry, list_persistent_entries, retire_persistent_entry, undo_persistent_change
from storage.repo.diff_log import log_diff, read_all_diffs
from storage.repo.notes import save_note, search_notes, get_pending_embeddings, mark_embedding_failed, update_note, delete_note, list_notes
from storage.repo.tasks import (
    create_task,
    list_tasks,
    get_task,
    complete_task,
    delete_task,
    update_task,
    expire_tasks,
    list_task_log,
)
from storage.repo.schedule import (
    create_schedule_entry,
    list_schedule_range,
    get_current_and_next,
    update_schedule_entry,
    delete_schedule_entry,
)
from storage.repo.sessions import (
    fetch_events_range,
    save_sessions,
    list_sessions_range,
)
from storage.repo.review import (
    list_completed_tasks,
    count_notes,
    list_tasks_due,
)
from storage.repo.compile_log import insert_compile_log, get_compile_log, list_compile_logs
from storage.repo.statements import insert_instruction_record, list_candidate_items, list_instruction_records
from storage.repo.schedule_drafts import (
    accept_entries, discard_draft, get_draft, get_open_draft, insert_draft, list_drafts, save_entries,
)
from storage.repo.llm_trace import count_llm_traces_since, insert_llm_trace, latest_sent_compile_log_id, list_llm_traces
from storage.repo.data_quality import (
    insert_monitoring_log,
    list_monitoring_log,
    insert_heartbeat_log,
    list_heartbeats,
    save_schedule_snapshot,
    save_day_record,
    days_with_snapshot,
    get_schedule_snapshot,
    days_with_day_record,
    insert_mark_wrong,
    session_bounds,
    last_day_record,
)
from storage.repo.retention import prune
__all__ = [
    "insert_monitoring_log", "list_monitoring_log", "insert_heartbeat_log", "list_heartbeats",
    "save_schedule_snapshot", "save_day_record", "days_with_snapshot", "get_schedule_snapshot", "days_with_day_record",
    "insert_mark_wrong", "prune", "session_bounds", "last_day_record",
    "insert_compile_log",
    "get_compile_log",
    "list_compile_logs",
    "insert_llm_trace",
    "insert_instruction_record", "list_candidate_items", "list_instruction_records",
    "accept_entries", "discard_draft", "get_draft", "get_open_draft", "insert_draft", "list_drafts", "save_entries",
    "count_llm_traces_since",
    "latest_sent_compile_log_id",
    "list_llm_traces",
    "list_completed_tasks",
    "count_notes",
    "list_tasks_due",
    "make_pool",
    "Event",
    "dedupe_key_for",
    "Embedder",
    "EmbeddingResult",
    "search",
    "SearchResult",
    "add_persistent_entry", "confirm_persistent_entry", "edit_persistent_entry", "list_persistent_entries", "retire_persistent_entry", "undo_persistent_change",
    "log_diff",
    "read_all_diffs",
    "parse_spool_file", "insert_many", "save_note", 
    "search_notes",
    "search",
    "index",
    "Retriever",
    "PostgresVectorStore",
    "create_task",
    "list_tasks",
    "get_task",
    "complete_task",
    "delete_task",
    "update_task",
    "expire_tasks",
    "list_task_log",
    "fetch_events_range",
    "save_sessions",
    "list_sessions_range",
    "get_pending_embeddings",
    "mark_embedding_failed",
    "update_note",
    "delete_note",
    "list_notes",
    "create_schedule_entry",
    "list_schedule_range",
    "get_current_and_next",
    "update_schedule_entry",
    "delete_schedule_entry",

]