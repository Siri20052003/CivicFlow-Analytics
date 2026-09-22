"""Typed domain model for municipal service cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Department(StrEnum):
    PUBLIC_WORKS = "Public Works"
    CODE_COMPLIANCE = "Code Compliance"
    PARKS = "Parks & Recreation"
    WATER = "Water Utilities"
    TRANSPORTATION = "Transportation"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


SLA_HOURS: dict[Priority, int] = {
    Priority.CRITICAL: 4,
    Priority.HIGH: 24,
    Priority.MEDIUM: 72,
    Priority.LOW: 120,
}


@dataclass(frozen=True, slots=True)
class ServiceCase:
    case_id: str
    opened_at: datetime
    department: Department
    service_type: str
    priority: Priority
    channel: str
    district: int
    status: Status
    assigned_team: str
    target_hours: int
    closed_at: datetime | None
    satisfaction_score: int | None

    @property
    def elapsed_hours(self) -> float | None:
        if self.closed_at is None:
            return None
        return (self.closed_at - self.opened_at).total_seconds() / 3600

    @property
    def met_sla(self) -> bool | None:
        elapsed = self.elapsed_hours
        return None if elapsed is None else elapsed <= self.target_hours

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["opened_at"] = self.opened_at.astimezone(UTC).isoformat()
        record["closed_at"] = self.closed_at.astimezone(UTC).isoformat() if self.closed_at else ""
        return record
