# knowledge/notes.py
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping
log = logging.getLogger(__name__)


SaveNote = Callable[
    [dict[str, Any]],
    Any | Awaitable[Any],
]
NoteSearch = Callable[
    [str, int],
    list[Mapping[str, Any]] | Awaitable[list[Mapping[str, Any]]],
]
SemanticSearch = Callable[
    ...,
    Any | Awaitable[Any],
]
@dataclass(frozen=True)
class Note:
    """
    One captured personal note.

    text:
        Exact note content supplied by the user.

    tags:
        Optional lightweight metadata for later retrieval.

    source:
        Where the note came from, e.g. panel, agent, import.

    created_at:
        UTC creation timestamp.
    """

    text: str
    tags: tuple[str, ...]
    source: str
    created_at: datetime
    id: int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "tags": list(self.tags),
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


class NoteCapture:
    """
    Capture and persist user notes.

    No model calls.
    No retrieval.
    No direct database access.
    No persistent-file access.
    """

    def __init__(
        self,
        *,
        save_note: SaveNote,
    ) -> None:
        self.save_note = save_note

    async def capture(
        self,
        text: str,
        *,
        tags: list[str] | tuple[str, ...] = (),
        source: str = "panel",
        created_at: datetime | None = None,
    ) -> Note | None:
        """
        Validate and save one note.

        Returns None on invalid input or persistence failure.
        """

        try:
            clean_text = self._normalize_text(text)

            if not clean_text:
                return None

            clean_source = self._normalize_source(
                source
            )

            if not clean_source:
                return None

            clean_tags = self._normalize_tags(
                tags
            )

            timestamp = (
                created_at
                if created_at is not None
                else datetime.now(timezone.utc)
            )

            if not self._is_aware(timestamp):
                return None

            timestamp = timestamp.astimezone(
                timezone.utc
            )

            note = Note(
                text=clean_text,
                tags=clean_tags,
                source=clean_source,
                created_at=timestamp,
            )

            payload = note.to_dict()

            result = self.save_note(payload)

            if inspect.isawaitable(result):
                result = await result

            if result is False or result is None:
                return None

            saved_id = None

            if isinstance(result, Mapping):
                saved_id = result.get("id")
            elif isinstance(result, (int, str)):
                saved_id = result

            return Note(
                id=saved_id,
                text=note.text,
                tags=note.tags,
                source=note.source,
                created_at=note.created_at,
            )

        except Exception:
            log.exception(
                "note capture failed"
            )
            return None

    @staticmethod
    def _normalize_text(
        text: Any,
    ) -> str:
        if not isinstance(text, str):
            return ""

        # Preserve the note itself; only remove surrounding whitespace.
        return text.strip()

    @staticmethod
    def _normalize_source(
        source: Any,
    ) -> str:
        if not isinstance(source, str):
            return ""

        return source.strip()

    @staticmethod
    def _normalize_tags(
        tags: Any,
    ) -> tuple[str, ...]:
        if not isinstance(
            tags,
            (list, tuple),
        ):
            return ()

        normalized: list[str] = []

        for tag in tags:
            if not isinstance(tag, str):
                continue

            tag = tag.strip()

            if tag and tag not in normalized:
                normalized.append(tag)

        return tuple(normalized)

    @staticmethod
    def _is_aware(
        value: datetime,
    ) -> bool:
        return (
            value.tzinfo is not None
            and value.utcoffset() is not None
        )


async def capture_note(
    text: str,
    *,
    save_note: SaveNote,
    tags: list[str] | tuple[str, ...] = (),
    source: str = "panel",
    created_at: datetime | None = None,
) -> Note | None:
    """
    Public I.1 entrypoint.
    """

    capture = NoteCapture(
        save_note=save_note,
    )

    return await capture.capture(
        text,
        tags=tags,
        source=source,
        created_at=created_at,
    )

@dataclass(frozen=True)
class NoteSearchResult:
    """
    One keyword-search result.

    The repository returns the stored note plus metadata.
    """

    id: int | str
    text: str
    tags: tuple[str, ...]
    source: str | None
    created_at: Any
    match: str | None = None
    score: float | None = None

    @classmethod
    def from_mapping(
        cls,
        row: Mapping[str, Any],
    ) -> "NoteSearchResult":
        return cls(
            id=row.get("id"),
            text=str(row.get("text", "")),
            tags=tuple(
                str(tag)
                for tag in (
                    row.get("tags", [])
                    or []
                )
                if tag is not None
            ),
            source=(
                str(row["source"])
                if row.get("source") is not None
                else None
            ),
            created_at=row.get("created_at"),
            match=(
                str(row["match"])
                if row.get("match") is not None
                else None
            ),
        )


class NoteRetriever:
    """
    Exact/keyword note retrieval.

    No embeddings.
    No model calls.
    No agent calls.
    No direct database access.
    """

    def __init__(
        self,
        *,
        search_notes: NoteSearch,
    ) -> None:
        self.search_notes = search_notes

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[NoteSearchResult]:
        """
        Search notes using plain keyword/exact text matching.

        The actual text-matching implementation belongs to the
        storage repository.
        """

        try:
            query = self._normalize_query(query)

            if not query:
                return []

            if limit <= 0:
                return []

            rows = self.search_notes(
                query,
                limit,
            )

            if inspect.isawaitable(rows):
                rows = await rows

            if not isinstance(rows, list):
                return []

            results: list[NoteSearchResult] = []

            for row in rows:
                if not isinstance(row, Mapping):
                    continue

                try:
                    result = NoteSearchResult.from_mapping(
                        row
                    )

                    if result.text:
                        results.append(result)

                except Exception:
                    continue

            return results

        except Exception:
            log.exception(
                "keyword note retrieval failed"
            )
            return []

    @staticmethod
    def _normalize_query(
        query: Any,
    ) -> str:
        if not isinstance(query, str):
            return ""

        return " ".join(
            query.strip().split()
        )


async def search_notes(
    query: str,
    *,
    search_notes_repo: NoteSearch,
    limit: int = 10,
) -> list[NoteSearchResult]:
    """
    Public I.2 entrypoint.
    """

    retriever = NoteRetriever(
        search_notes=search_notes_repo,
    )

    return await retriever.search(
        query,
        limit=limit,
    )

@dataclass(frozen=True)
class SemanticNoteResult:
    """
    One semantic-search result returned by B.4.
    """

    id: int | str
    text: str
    score: float
    metadata: dict[str, Any]

    @classmethod
    def from_result(
        cls,
        result: Any,
    ) -> "SemanticNoteResult | None":
        try:
            # B.4 SearchResult contract.
            if hasattr(result, "content"):
                note_id = getattr(result, "id")
                text = getattr(result, "content")
                score = getattr(result, "score")
                metadata = getattr(
                    result,
                    "metadata",
                    {},
                )

            # Also tolerate a repository-style mapping so the
            # retrieval boundary remains easy to mock/test.
            elif isinstance(result, Mapping):
                note_id = result.get("id")
                text = result.get(
                    "content",
                    result.get("text", ""),
                )
                score = result.get("score", 0.0)
                metadata = result.get(
                    "metadata",
                    {},
                )

            else:
                return None

            if note_id is None:
                return None

            if not isinstance(text, str):
                return None

            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0

            if not isinstance(metadata, Mapping):
                metadata = {}

            return cls(
                id=note_id,
                text=text,
                score=score,
                metadata=dict(metadata),
            )

        except Exception:
            return None


class SemanticNoteRetriever:
    """
    Semantic retrieval over notes through the B.4 retrieval boundary.

    No embeddings.
    No vector database access.
    No model calls.
    No direct storage access.
    """

    def __init__(
        self,
        *,
        search: SemanticSearch,
    ) -> None:
        self.search = search

    async def search_notes(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[SemanticNoteResult]:
        """
        Retrieve semantically related notes.

        Retrieval failures degrade to an empty result set.
        """

        try:
            query = self._normalize_query(query)

            if not query:
                return []

            if limit <= 0:
                return []

            raw_results = self.search(
                table="notes",
                query=query,
                k=limit,
                filters=None,
            )

            if inspect.isawaitable(raw_results):
                raw_results = await raw_results

            if not isinstance(raw_results, list):
                return []

            results: list[SemanticNoteResult] = []

            for raw_result in raw_results:
                result = SemanticNoteResult.from_result(
                    raw_result
                )

                if result is None:
                    continue

                if not result.text.strip():
                    continue

                results.append(result)

            return results

        except Exception:
            log.exception(
                "semantic note retrieval failed"
            )
            return []

    @staticmethod
    def _normalize_query(
        query: Any,
    ) -> str:
        if not isinstance(query, str):
            return ""

        return query.strip()

async def search_notes_combined(
    query: str,
    *,
    search_notes_repo: NoteSearch,
    semantic_search: SemanticSearch,
    limit: int = 10,
) -> list[NoteSearchResult]:
    if not isinstance(query, str):
        return []
    query = query.strip()
    if not query or limit <= 0:
        return []

    keyword_results = await search_notes(
        query,
        search_notes_repo=search_notes_repo,
        limit=limit,
    )

    semantic_results = await SemanticNoteRetriever(
        search=semantic_search,
    ).search_notes(
        query,
        limit=limit,
    )

    combined: list[NoteSearchResult] = []
    seen_ids: set[int | str] = set()

    for result in keyword_results:
        if len(combined) >= limit:
            break

        combined.append(
            NoteSearchResult(
                id=result.id,
                text=result.text,
                tags=result.tags,
                source=result.source,
                created_at=result.created_at,
                match="keyword",
                score=None,
            )
        )
        seen_ids.add(result.id)

    for result in semantic_results:
        if len(combined) >= limit:
            break

        if result.id in seen_ids:
            continue

        metadata = result.metadata

        combined.append(
            NoteSearchResult(
                id=result.id,
                text=result.text,
                tags=tuple(metadata.get("tags") or ()),
                source=metadata.get("source"),
                created_at=metadata.get("created_at"),
                match="semantic",
                score=result.score,
            )
        )
        seen_ids.add(result.id)

    return combined