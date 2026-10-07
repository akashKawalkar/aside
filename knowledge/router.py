#knowledge/router.py
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

log = logging.getLogger(__name__)


SkillSearch = Callable[
    ...,
    Any | Awaitable[Any],
]


@dataclass(frozen=True)
class SkillMatch:
    id: int | str
    name: str
    content: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SkillRoute:
    """
    Result of routing one request.

    relevant:
        Whether the request was detected as skill-relevant.

    skills:
        Matching skills returned through B.4.
    """

    relevant: bool
    skills: tuple[SkillMatch, ...]
    trigger: str | None = None


class SkillRouter:
    """
    Detect skill-relevant requests and retrieve matching skills.

    No model calls.
    No embedding calls.
    No direct database/vector access.
    """

    DEFAULT_TRIGGERS = frozenset(
        {
            "rag",
            "skill",
            "format",
            "template",
            "how do i usually",
            "how i want",
            "my usual format",
        }
    )

    def __init__(
        self,
        *,
        search_skills: SkillSearch,
        triggers: set[str] | frozenset[str] | None = None,
    ) -> None:
        self.search_skills = search_skills
        self.triggers = frozenset(
            trigger.casefold().strip()
            for trigger in (
                triggers or self.DEFAULT_TRIGGERS
            )
            if isinstance(trigger, str)
            and trigger.strip()
        )

    async def route(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> SkillRoute:
        """
        Detect whether the request is skill-relevant and, when it is,
        retrieve matching skills through B.4.

        Failures degrade to an empty route.
        """

        try:
            query = self._normalize_query(query)

            if not query:
                return SkillRoute(
                    relevant=False,
                    skills=(),
                )

            if limit <= 0:
                return SkillRoute(
                    relevant=False,
                    skills=(),
                )

            trigger = self._detect_trigger(query)

            if trigger is None:
                return SkillRoute(
                    relevant=False,
                    skills=(),
                )

            raw_results = self.search_skills(
                table="skills",
                query=query,
                k=limit,
                filters=None,
            )

            if inspect.isawaitable(raw_results):
                raw_results = await raw_results

            if not isinstance(
                raw_results,
                list,
            ):
                return SkillRoute(
                    relevant=True,
                    skills=(),
                    trigger=trigger,
                )

            matches: list[SkillMatch] = []

            for raw in raw_results:
                match = self._normalize_result(raw)

                if match is not None:
                    matches.append(match)

            return SkillRoute(
                relevant=True,
                skills=tuple(matches),
                trigger=trigger,
            )

        except Exception:
            log.exception(
                "skill routing failed"
            )

            return SkillRoute(
                relevant=False,
                skills=(),
            )

    def _detect_trigger(
        self,
        query: str,
    ) -> str | None:
        text = query.casefold()

        for trigger in sorted(
            self.triggers,
            key=len,
            reverse=True,
        ):
            if trigger in text:
                return trigger

        return None

    @staticmethod
    def _normalize_query(
        query: Any,
    ) -> str:
        if not isinstance(query, str):
            return ""

        return " ".join(
            query.strip().split()
        )

    @staticmethod
    def _normalize_result(
        result: Any,
    ) -> SkillMatch | None:
        try:
            if isinstance(result, Mapping):
                skill_id = result.get("id")
                name = result.get(
                    "name",
                    "",
                )
                content = result.get(
                    "content",
                    "",
                )
                score = result.get(
                    "score",
                    0.0,
                )
                metadata = result.get(
                    "metadata",
                    {},
                )

            else:
                skill_id = getattr(
                    result,
                    "id",
                    None,
                )
                name = getattr(
                    result,
                    "name",
                    "",
                )
                content = getattr(
                    result,
                    "content",
                    "",
                )
                score = getattr(
                    result,
                    "score",
                    0.0,
                )
                metadata = getattr(
                    result,
                    "metadata",
                    {},
                )

            if skill_id is None:
                return None

            if not isinstance(
                name,
                str,
            ):
                name = str(name)

            if not isinstance(
                content,
                str,
            ):
                return None

            try:
                score = float(score)
            except (TypeError, ValueError):
                score = 0.0

            if not isinstance(
                metadata,
                Mapping,
            ):
                metadata = {}

            if not content.strip():
                return None

            return SkillMatch(
                id=skill_id,
                name=name,
                content=content,
                score=score,
                metadata=dict(metadata),
            )

        except Exception:
            return None


async def route_skill(
    query: str,
    *,
    search_skills: SkillSearch,
    limit: int = 5,
) -> SkillRoute:
    """
    Public I.5 entrypoint.
    """

    router = SkillRouter(
        search_skills=search_skills,
    )

    return await router.route(
        query,
        limit=limit,
    )