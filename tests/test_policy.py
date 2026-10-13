from pathlib import Path
from datetime import datetime, timedelta

import pytest

from shorts_agent.database import Database
from shorts_agent.models import ClipSegment, PublicationRequest, RightsRecord


def test_source_requires_rights_evidence(tmp_path: Path) -> None:
    media = tmp_path / "owned.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()

    with pytest.raises(ValueError, match="Rights basis, evidence"):
        database.register_source(media, RightsRecord("", "", ""))


def test_registered_source_retains_rights_record(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    rights = RightsRecord("written permission", "permission-2026-01", "editor")

    registered = database.register_source(media, rights)
    loaded = database.get_source(registered.source_id)

    assert loaded.rights == rights
    assert loaded.path == media.resolve()


@pytest.mark.parametrize("visibility", ["public", "scheduled"])
def test_publication_requires_human_approval(visibility: str) -> None:
    with pytest.raises(PermissionError, match="Human approval"):
        PublicationRequest(visibility).validate()

    scheduled_for = datetime.now() + timedelta(days=1) if visibility == "scheduled" else None
    PublicationRequest(visibility, approved_by="channel-owner", scheduled_for=scheduled_for).validate()


def test_scheduled_publication_requires_a_future_time() -> None:
    with pytest.raises(ValueError, match="requires a date and time"):
        PublicationRequest("scheduled", "channel-owner").validate()


def test_private_publication_is_default_and_needs_no_approval() -> None:
    request = PublicationRequest()

    request.validate()

    assert request.visibility == "private"


def test_clip_duration_is_limited_to_sixty_seconds() -> None:
    with pytest.raises(ValueError, match="must not exceed 60"):
        ClipSegment(0, 61).validate()


def test_database_tracks_rendered_clips_and_publication_queue(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    source = database.register_source(
        media, RightsRecord("written permission", "permission-2026-01", "editor")
    )
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"rendered")

    clip_id = database.record_clip(source.source_id, 2, 42, output)
    request_id = database.enqueue_publication(clip_id, "A title", "Description", "private", None)

    assert database.metrics() == {"sources": 1, "clips": 1, "queued": 1}
    assert database.list_clips()[0]["clip_id"] == clip_id
    assert database.list_publication_queue()[0]["request_id"] == request_id
    assert [event["event"] for event in database.list_activity(3)] == [
        "publication_queued",
        "clip_rendered",
        "source_registered",
    ]


def test_database_persists_approved_schedule_time(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    source = database.register_source(
        media, RightsRecord("written permission", "permission-2026-01", "editor")
    )
    clip_id = database.record_clip(source.source_id, 1, 20, tmp_path / "clip.mp4")
    scheduled_for = datetime.now() + timedelta(days=1)

    database.enqueue_publication(
        clip_id, "Scheduled title", "Description", "scheduled", "channel-owner", scheduled_for
    )

    assert database.list_publication_queue()[0]["scheduled_for"] == scheduled_for.isoformat(
        timespec="minutes"
    )


def test_publication_queue_rejects_unapproved_public_request(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    source = database.register_source(
        media, RightsRecord("written permission", "permission-2026-01", "editor")
    )
    clip_id = database.record_clip(source.source_id, 1, 20, tmp_path / "clip.mp4")

    with pytest.raises(PermissionError, match="Human approval"):
        database.enqueue_publication(clip_id, "A title", "", "public", None)