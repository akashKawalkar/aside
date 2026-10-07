from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from compiler.persistent_file import PersistentFile, PersistentFileStore
from storage import SearchResult


@dataclass(frozen=True)
class CompileItem:
    """
    One complete piece of context that can be included or omitted
    atomically.
    """

    text: str
    tier: int
    name: str


@dataclass(frozen=True)
class CompileResult:
    """
    Final packed context plus explicit omission notes.
    """

    context: str
    omitted: tuple[str, ...]


@dataclass(frozen=True)
class DueItem:
    """
    Generic due-item representation (a task or a schedule entry).

    The compiler does not know which database table produced it.
    """

    text: str
    due_at: Any
    kind: str = "task"


Retriever = Callable[
    [str, str, int, dict[str, Any] | None],
    list[SearchResult] | Awaitable[list[SearchResult]],
]


class ContextCompiler:
    """
    Assemble the context that matters right now.

    Priority:
        1. Current session      — Tier 1, raw/uncompressed
        2. Due-soon tasks       — full
        3. Aged/pending tasks   — collapsed to count
        4. Persistent file      — Tier 3
        5. Relevant notes       — Tier 2
        6. Episodic history     — Tier 2, lowest priority

    No model calls.
    No agent calls.
    No direct database access.
    """

    def __init__(
        self,
        *,
        persistent_store: PersistentFileStore,
        retriever: Retriever | None = None,
        max_chars: int = 12000,
    ) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be greater than 0")

        self.persistent_store = persistent_store
        self.retriever = retriever
        self.max_chars = max_chars

    async def compile(
        self,
        *,
        current_session: str,
        due_items: Iterable[DueItem] = (),
        aged_pending_count: int = 0,
        query: str | None = None,
        note_limit: int = 5,
        history_limit: int = 5,
    ) -> CompileResult:
        """
        Build one packed context.

        current_session is always kept raw and is never internally
        summarized or rewritten.

        Every item that cannot be included is explicitly reported.
        """

        parts: list[str] = []
        omitted: list[str] = []

        # Tier 1: mandatory current-session context.
        session_text = self._safe_text(current_session)

        if session_text:
            parts.append(
                self._section(
                    "TIER 1 — CURRENT SESSION",
                    session_text,
                )
            )
        else:
            omitted.append(
                "[Current session omitted: no usable session data]"
            )

        # Reserve the actual remaining budget.
        used = self._length(parts)

        # Priority 1: due-soon tasks, full.
        for index, item in enumerate(due_items, 1):
            text = self._format_due_item(item)

            if not text:
                omitted.append(
                    f"[Due item {index} omitted: empty]"
                )
                continue

            candidate = self._section(
                "DUE-SOON TASKS",
                text,
            )

            if self._fits(
                parts,
                candidate,
                used,
            ):
                parts.append(candidate)
                used += len(candidate)
            else:
                omitted.append(
                    f"[Due item {index} omitted: context budget]"
                )

        # Priority 2: aged/pending tasks collapsed to a count.
        if aged_pending_count > 0:
            collapsed = (
                f"[{aged_pending_count} aged/pending "
                f"tasks omitted; represented as a count]"
            )

            if self._fits(parts, collapsed, used):
                parts.append(
                    self._section(
                        "AGED / PENDING TASKS",
                        collapsed,
                    )
                )
                used += len(collapsed)
            else:
                omitted.append(
                    "[Aged/pending task count omitted: "
                    "context budget]"
                )

        # Priority 3: persistent file.
        persistent = self.persistent_store.snapshot()
        persistent_text = self._format_persistent_file(
            persistent
        )

        if persistent_text:
            candidate = self._section(
                "TIER 3 — PERSISTENT FILE",
                persistent_text,
            )

            if self._fits(parts, candidate, used):
                parts.append(candidate)
                used += len(candidate)
            else:
                omitted.append(
                    "[Persistent file omitted: context budget]"
                )
        else:
            omitted.append(
                "[Persistent file omitted: empty]"
            )

        # If no query exists, semantic Tier 2 retrieval cannot be
        # meaningfully performed.
        if not query or not query.strip():
            omitted.append(
                "[Relevant notes omitted: no retrieval query]"
            )
            omitted.append(
                "[Episodic history omitted: no retrieval query]"
            )
            return self._finalize(parts, omitted)

        # No retriever configured yet.
        if self.retriever is None:
            omitted.append(
                "[Relevant notes omitted: retriever unavailable]"
            )
            omitted.append(
                "[Episodic history omitted: retriever unavailable]"
            )
            return self._finalize(parts, omitted)

        # Priority 4: relevant notes.
        notes = await self._retrieve(
            table="notes",
            query=query,
            k=note_limit,
            filters=None,
        )

        if notes:
            included = 0

            for index, result in enumerate(notes, 1):
                text = self._format_search_result(
                    result,
                    index=index,
                )

                candidate = self._section(
                    "RELEVANT NOTES",
                    text,
                )

                if self._fits(parts, candidate, used):
                    parts.append(candidate)
                    used += len(candidate)
                    included += 1
                else:
                    omitted.append(
                        f"[{len(notes) - included} relevant notes "
                        f"omitted: context budget]"
                    )
                    break

            if included == 0:
                omitted.append(
                    "[Relevant notes omitted: context budget]"
                )
        else:
            omitted.append(
                "[Relevant notes omitted: no results]"
            )

        # Priority 5: episodic history.
        #
        # The concrete B.4 backend can decide how "sessions" are searched.
        # We do not import a future repository here.
        history = await self._retrieve(
            table="sessions",
            query=query,
            k=history_limit,
            filters=None,
        )

        if history:
            included = 0

            for index, result in enumerate(history, 1):
                text = self._format_search_result(
                    result,
                    index=index,
                )

                candidate = self._section(
                    "EPISODIC HISTORY",
                    text,
                )

                if self._fits(parts, candidate, used):
                    parts.append(candidate)
                    used += len(candidate)
                    included += 1
                else:
                    omitted.append(
                        f"[{len(history) - included} episodic "
                        f"history items omitted: context budget]"
                    )
                    break

            if included == 0:
                omitted.append(
                    "[Episodic history omitted: context budget]"
                )
        else:
            omitted.append(
                "[Episodic history omitted: no results]"
            )

        return self._finalize(parts, omitted)

    async def _retrieve(
        self,
        *,
        table: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None,
    ) -> list[SearchResult]:
        """
        Call the injected B.4 retrieval boundary.

        Retrieval failure becomes an omission note instead of
        taking down compilation.
        """

        try:
            result = self.retriever(
                table,
                query,
                k,
                filters,
            )

            if inspect.isawaitable(result):
                result = await result

            if not isinstance(result, list):
                return []

            return [
                item
                for item in result
                if isinstance(item, SearchResult)
            ]

        except Exception:
            return []

    def _finalize(
        self,
        parts: list[str],
        omitted: list[str],
    ) -> CompileResult:
        if omitted:
            parts.append(
                self._section(
                    "EXPLICIT OMISSIONS",
                    "\n".join(omitted),
                )
            )

        return CompileResult(
            context="\n\n".join(parts),
            omitted=tuple(omitted),
        )

    def _fits(
        self,
        parts: list[str],
        candidate: str,
        used: int,
    ) -> bool:
        separator = 2 if parts else 0

        return (
            used
            + separator
            + len(candidate)
            <= self.max_chars
        )

    @staticmethod
    def _length(parts: list[str]) -> int:
        if not parts:
            return 0

        return sum(len(part) for part in parts) + (
            2 * (len(parts) - 1)
        )

    @staticmethod
    def _safe_text(value: Any) -> str:
        if not isinstance(value, str):
            return ""

        return value.strip()

    @staticmethod
    def _section(
        title: str,
        body: str,
    ) -> str:
        return f"## {title}\n{body.strip()}"

    @staticmethod
    def _format_due_item(
        item: DueItem,
    ) -> str:
        try:
            text = item.text.strip()
        except Exception:
            return ""

        if not text:
            return ""

        return (
            f"- [{item.kind}] {text}\n"
            f"  due_at: {item.due_at}"
        )

    @staticmethod
    def _format_persistent_file(
        persistent: PersistentFile,
    ) -> str:
        sections: list[str] = []

        if persistent.static:
            sections.append(
                "STATIC\n"
                + ContextCompiler._format_mapping(
                    persistent.static
                )
            )

        if persistent.dynamic:
            sections.append(
                "DYNAMIC\n"
                + ContextCompiler._format_mapping(
                    persistent.dynamic
                )
            )

        return "\n\n".join(sections)

    @staticmethod
    def _format_mapping(
        values: dict[str, Any],
    ) -> str:
        lines: list[str] = []

        for key, value in values.items():
            lines.append(f"- {key}: {value}")

        return "\n".join(lines)

    @staticmethod
    def _format_search_result(
        result: SearchResult,
        *,
        index: int,
    ) -> str:
        metadata = ""

        if result.metadata:
            metadata = (
                f"\n  metadata: {result.metadata}"
            )

        return (
            f"- result_{index} "
            f"(score={result.score:.4f})\n"
            f"  {result.content}"
            f"{metadata}"
        )


async def compile(
    *,
    current_session: str,
    persistent_store: PersistentFileStore,
    due_items: Iterable[DueItem] = (),
    aged_pending_count: int = 0,
    query: str | None = None,
    retriever: Retriever | None = None,
    note_limit: int = 5,
    history_limit: int = 5,
    max_chars: int = 12000,
) -> CompileResult:
    """
    Public compiler entrypoint.
    """

    compiler = ContextCompiler(
        persistent_store=persistent_store,
        retriever=retriever,
        max_chars=max_chars,
    )

    return await compiler.compile(
        current_session=current_session,
        due_items=due_items,
        aged_pending_count=aged_pending_count,
        query=query,
        note_limit=note_limit,
        history_limit=history_limit,
    )