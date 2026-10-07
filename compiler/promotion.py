from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable


@dataclass(frozen=True)
class PromotionCandidate:
    """
    A dynamic item that has become eligible for promotion.

    Promotion is NOT performed here. This object represents a
    suggestion that can later be presented for user confirmation.
    """

    key: str
    required_references: int
    reference_days: tuple[date, ...]


class PromotionEngine:
    """
    Determines which dynamic items are eligible for promotion.

    No database access.
    No file writes.
    No model calls.
    No automatic promotion.
    """

    DEFAULT_BASE_THRESHOLD = 10
    DEFAULT_EXCITEMENT = 1.0
    WINDOW_DAYS = 30

    def __init__(
        self,
        *,
        base_threshold: int = DEFAULT_BASE_THRESHOLD,
        excitement: float = DEFAULT_EXCITEMENT,
    ) -> None:
        if base_threshold <= 0:
            raise ValueError(
                "base_threshold must be greater than 0"
            )

        if excitement <= 0:
            raise ValueError(
                "excitement must be greater than 0"
            )

        self.base_threshold = base_threshold
        self.excitement = excitement

    @property
    def required_references(self) -> int:
        """
        Calculate the reference threshold from the excitement knob.

        Higher excitement -> lower threshold.
        Lower excitement -> higher threshold.
        """

        return max(
            1,
            int(self.base_threshold / self.excitement),
        )

    def is_eligible(
        self,
        reference_days: Iterable[date],
        *,
        today: date | None = None,
    ) -> bool:
        """
        Return whether an item has enough distinct reference days
        within the rolling 30-day window.
        """

        days = self._valid_reference_days(
            reference_days,
            today=today,
        )

        return len(days) >= self.required_references

    def evaluate(
        self,
        *,
        key: str,
        reference_days: Iterable[date],
        today: date | None = None,
    ) -> PromotionCandidate | None:
        """
        Return a promotion suggestion when the item is eligible.

        Returns None when the threshold has not been reached.
        """

        if not key or not key.strip():
            return None

        days = self._valid_reference_days(
            reference_days,
            today=today,
        )

        if len(days) < self.required_references:
            return None

        return PromotionCandidate(
            key=key,
            required_references=self.required_references,
            reference_days=tuple(days),
        )

    def evaluate_many(
        self,
        references: dict[str, Iterable[date]],
        *,
        today: date | None = None,
    ) -> list[PromotionCandidate]:
        """
        Evaluate multiple dynamic items independently.
        """

        candidates: list[PromotionCandidate] = []

        for key, reference_days in references.items():
            candidate = self.evaluate(
                key=key,
                reference_days=reference_days,
                today=today,
            )

            if candidate is not None:
                candidates.append(candidate)

        return candidates

    def _valid_reference_days(
        self,
        reference_days: Iterable[date],
        *,
        today: date | None,
    ) -> list[date]:
        if today is None:
            today = date.today()

        window_start = (
            today - timedelta(days=self.WINDOW_DAYS - 1)
        )

        unique_days: set[date] = set()

        for value in reference_days:
            if not isinstance(value, date):
                continue

            if (
                window_start
                <= value
                <= today
            ):
                unique_days.add(value)

        return sorted(unique_days)