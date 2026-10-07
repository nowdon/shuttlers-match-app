"""Backend-neutral results returned by storage write operations."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StorageResult:
    """Normalized result for one executed statement."""

    rows: list[dict] = field(default_factory=list)
    changes: int | None = None
    last_row_id: int | None = None
