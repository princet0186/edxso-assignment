"""The contract every pipeline stage implements.

A stage receives a StageContext, does its work creator by creator (committing as it goes so
a crash or quota stop never loses finished work), and returns a StageReport of counts.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlmodel import Session

from outreach.config import Secrets, Settings
from outreach.models import PipelineError
from outreach.sources.youtube import QuotaTracker


@dataclass
class StageContext:
    session: Session
    settings: Settings
    secrets: Secrets
    run_id: int
    youtube_quota: QuotaTracker
    item_limit: int | None = None

    def limited[T](self, items: list[T]) -> list[T]:
        """Applies --limit for quick trial runs; None means process everything."""
        return items if self.item_limit is None else items[: self.item_limit]

    def record_error(self, stage: str, message: str, creator_id: int | None = None) -> None:
        self.session.add(
            PipelineError(run_id=self.run_id, creator_id=creator_id, stage=stage, message=message)
        )


@dataclass(frozen=True)
class StageReport:
    counts: dict[str, int] = field(default_factory=dict)


StageHandler = Callable[[StageContext], StageReport]
