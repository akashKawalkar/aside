# knowledge/skills.py
from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

log = logging.getLogger(__name__)


SaveSkill = Callable[
    [dict[str, Any]],
    Any | Awaitable[Any],
]


@dataclass(frozen=True)
class Skill:
    """
    One hand-written situational format/instruction.

    name:
        Human-readable skill name.

    content:
        Exact hand-written format or instruction.

    tags:
        Lightweight metadata used to identify situations.

    metadata:
        Additional structured information.

    created_at:
        UTC creation timestamp.
    """

    name: str
    content: str
    tags: tuple[str, ...]
    metadata: dict[str, Any]
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "content": self.content,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
        }


class SkillLibrary:
    """
    Store hand-written skills.

    No model calls.
    No embedding.
    No vector search.
    No direct database access.
    """

    def __init__(
        self,
        *,
        save_skill: SaveSkill,
    ) -> None:
        self.save_skill = save_skill

    async def add(
        self,
        *,
        name: str,
        content: str,
        tags: list[str] | tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> Skill | None:
        """
        Validate and persist one hand-written skill.

        Returns None on invalid input or persistence failure.
        """

        try:
            clean_name = self._normalize_text(name)
            clean_content = self._normalize_text(content)

            if not clean_name:
                return None

            if not clean_content:
                return None

            clean_tags = self._normalize_tags(tags)

            clean_metadata = self._normalize_metadata(
                metadata
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

            skill = Skill(
                name=clean_name,
                content=clean_content,
                tags=clean_tags,
                metadata=clean_metadata,
                created_at=timestamp,
            )

            result = self.save_skill(
                skill.to_dict()
            )

            if inspect.isawaitable(result):
                result = await result

            if result is False:
                return None

            return skill

        except Exception:
            log.exception(
                "skill save failed"
            )
            return None

    @staticmethod
    def _normalize_text(
        value: Any,
    ) -> str:
        if not isinstance(value, str):
            return ""

        return value.strip()

    @staticmethod
    def _normalize_tags(
        tags: Any,
    ) -> tuple[str, ...]:
        if not isinstance(
            tags,
            (list, tuple),
        ):
            return ()

        result: list[str] = []

        for tag in tags:
            if not isinstance(tag, str):
                continue

            tag = tag.strip()

            if tag and tag not in result:
                result.append(tag)

        return tuple(result)

    @staticmethod
    def _normalize_metadata(
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not isinstance(metadata, Mapping):
            return {}

        return {
            str(key): SkillLibrary._copy_value(value)
            for key, value in metadata.items()
        }

    @staticmethod
    def _copy_value(
        value: Any,
    ) -> Any:
        if value is None:
            return None

        if isinstance(value, dict):
            return {
                str(key): SkillLibrary._copy_value(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [
                SkillLibrary._copy_value(item)
                for item in value
            ]

        if isinstance(
            value,
            (str, int, float, bool),
        ):
            return value

        if isinstance(value, datetime):
            return value.isoformat()

        return str(value)

    @staticmethod
    def _is_aware(
        value: datetime,
    ) -> bool:
        return (
            value.tzinfo is not None
            and value.utcoffset() is not None
        )


async def add_skill(
    *,
    name: str,
    content: str,
    save_skill: SaveSkill,
    tags: list[str] | tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
    created_at: datetime | None = None,
) -> Skill | None:
    """
    Public I.4 entrypoint.
    """

    library = SkillLibrary(
        save_skill=save_skill,
    )

    return await library.add(
        name=name,
        content=content,
        tags=tags,
        metadata=metadata,
        created_at=created_at,
    )

def triggers(use_when: str | None) -> list[str]:
    """`skills.use_when` is a comma-separated list of trigger words/phrases."""
    return [t.strip() for t in (use_when or "").split(",") if t.strip()]


def match_score(use_when: str | None, text: str | None) -> int:
    """How many triggers appear in `text` as whole words/phrases ("gym" must not hit "gymkhana")."""
    if not text:
        return 0
    return sum(1 for t in triggers(use_when) if re.search(rf"(?<!\w){re.escape(t)}(?!\w)", text, re.IGNORECASE))


async def load_relevant_skills(pool, text: str | None, limit: int = 2) -> list[dict[str, Any]]:
    from storage.repo.skills import list_skills
    skills = await list_skills(pool)

    scored = [(match_score(s.get("use_when"), text), s) for s in skills]
    scored = [(score, s) for score, s in scored if score > 0]
    scored.sort(key=lambda x: (x[0], x[1].get("usage_count", 0)), reverse=True)
    return [s for _, s in scored[:limit]]