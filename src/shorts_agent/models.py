from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RightsRecord:
    basis: str
    evidence: str
    confirmed_by: str

    def validate(self) -> None:
        if not all(value.strip() for value in (self.basis, self.evidence, self.confirmed_by)):
            raise ValueError("Rights basis, evidence, and confirmer are required")


@dataclass(frozen=True)
class SourceAsset:
    source_id: str
    path: Path
    rights: RightsRecord


@dataclass(frozen=True)
class ClipSegment:
    start_seconds: float
    end_seconds: float

    def validate(self) -> None:
        duration = self.end_seconds - self.start_seconds
        if self.start_seconds < 0 or duration <= 0:
            raise ValueError("Clip timestamps must have a positive duration")
        if duration > 60:
            raise ValueError("Shorts clip duration must not exceed 60 seconds")


@dataclass(frozen=True)
class PublicationRequest:
    visibility: str = "private"
    approved_by: str | None = None
    scheduled_for: datetime | None = None

    def validate(self) -> None:
        if self.visibility not in {"private", "public", "scheduled"}:
            raise ValueError("Visibility must be private, public, or scheduled")
        if self.visibility != "private" and not (self.approved_by or "").strip():
            raise PermissionError("Human approval is required for public or scheduled publication")
        if self.visibility == "scheduled":
            if self.scheduled_for is None:
                raise ValueError("Scheduled publication requires a date and time")
            if self.scheduled_for <= datetime.now():
                raise ValueError("Scheduled publication must be in the future")