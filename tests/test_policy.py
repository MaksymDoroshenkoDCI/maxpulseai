import json
import ssl
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from shorts_agent.database import Database
from shorts_agent.discovery import YouTubeCreativeCommonsDiscovery
from shorts_agent.models import ClipSegment, PublicationRequest, RightsRecord
from shorts_agent.youtube import (
    ChannelMismatchError,
    build_video_resource,
    verify_target_channel,
)


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


def test_candidate_approval_requires_evidence_and_named_reviewer(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()

    with pytest.raises(ValueError, match="attribution, and approver"):
        database.approve_candidate(
            "video-1", "Title", "https://youtube.com/watch?v=video-1", "Channel",
            "creativeCommon", "", "Attribution", "",
        )

    database.approve_candidate(
        "video-1", "Title", "https://youtube.com/watch?v=video-1", "Channel",
        "creativeCommon", "License details checked", "Title by Channel, link", "Channel owner",
    )

    approval = database.list_candidate_approvals()[0]
    assert approval["approved_by"] == "Channel owner"
    assert approval["rights_evidence"] == "License details checked"
    assert approval["attribution_text"] == "Title by Channel, link"
    assert database.list_activity(1)[0]["event"] == "candidate_approved_for_clipping"


def test_candidate_approval_rejects_non_cc_marker(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()

    with pytest.raises(ValueError, match="Creative Commons search"):
        database.approve_candidate(
            "video-1", "Title", "https://youtube.com/watch?v=video-1", "Channel",
            "unverified", "License details checked", "Title by Channel, link", "Channel owner",
        )


def test_youtube_search_requests_creative_commons_filter() -> None:
    payload = {
        "items": [
            {
                "id": {"videoId": "video-1"},
                "snippet": {"title": "CC video", "channelTitle": "Creator"},
            }
        ]
    }
    response = BytesIO(json.dumps(payload).encode())
    with patch("shorts_agent.discovery.urlopen", return_value=response) as mocked_urlopen:
        response.__enter__ = lambda: response
        response.__exit__ = lambda *_: None
        candidates = YouTubeCreativeCommonsDiscovery("test-key").search("science", 5)

    request_url = mocked_urlopen.call_args.args[0]
    ssl_context = mocked_urlopen.call_args.kwargs["context"]
    assert parse_qs(urlparse(request_url).query)["videoLicense"] == ["creativeCommon"]
    assert ssl_context.verify_mode == ssl.CERT_REQUIRED
    assert ssl_context.check_hostname
    assert candidates[0].license_type == "creativeCommon"
    assert candidates[0].url == "https://www.youtube.com/watch?v=video-1"


def test_video_resource_defaults_to_private_and_keeps_metadata() -> None:
    resource = build_video_resource(" A Short ", " Description ", PublicationRequest())

    assert resource["snippet"]["title"] == "A Short"
    assert resource["snippet"]["description"] == "Description"
    assert resource["status"]["privacyStatus"] == "private"


def test_scheduled_video_resource_uses_private_and_publish_at() -> None:
    scheduled_for = datetime.now() + timedelta(days=1)
    resource = build_video_resource(
        "Scheduled short",
        "Description",
        PublicationRequest("scheduled", "channel-owner", scheduled_for),
    )

    assert resource["status"]["privacyStatus"] == "private"
    assert resource["status"]["publishAt"].endswith("Z")


def test_public_upload_resource_requires_named_approval() -> None:
    with pytest.raises(PermissionError, match="Human approval"):
        build_video_resource("Public short", "Description", PublicationRequest("public"))


def test_oauth_channel_must_match_configured_channel() -> None:
    class FakeRequest:
        def execute(self) -> dict[str, object]:
            return {"items": [{"id": "other-channel", "snippet": {"title": "Other"}}]}

    class FakeChannels:
        def list(self, **kwargs: object) -> FakeRequest:
            assert kwargs == {"part": "id,snippet", "mine": True}
            return FakeRequest()

    class FakeService:
        def channels(self) -> FakeChannels:
            return FakeChannels()

    with pytest.raises(ChannelMismatchError, match="not found among authorized channels"):
        verify_target_channel(FakeService(), "expected-channel")


def test_oauth_selects_expected_channel_from_multiple_channels() -> None:
    class FakeRequest:
        def execute(self) -> dict[str, object]:
            return {
                "items": [
                    {"id": "other-channel", "snippet": {"title": "Other"}},
                    {"id": "target-channel", "snippet": {"title": "My Channel"}},
                ]
            }

    class FakeChannels:
        def list(self, **kwargs: object) -> FakeRequest:
            return FakeRequest()

    class FakeService:
        def channels(self) -> FakeChannels:
            return FakeChannels()

    assert verify_target_channel(FakeService(), "target-channel") == (
        "target-channel",
        "My Channel",
    )


def test_database_records_upload_and_prevents_duplicate_upload(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    source = database.register_source(
        media, RightsRecord("owned", "owner record", "channel-owner")
    )
    clip_id = database.record_clip(source.source_id, 0, 20, tmp_path / "clip.mp4")
    request_id = database.enqueue_publication(clip_id, "A Short", "Description", "private", None)

    database.mark_publication_uploaded(request_id, "youtube-video-123")

    queued_item = database.list_publication_queue()[0]
    assert queued_item["status"] == "uploaded"
    assert queued_item["youtube_video_id"] == "youtube-video-123"
    assert queued_item["uploaded_at"]
    with pytest.raises(ValueError, match="already been uploaded"):
        database.mark_publication_uploaded(request_id, "youtube-video-456")


def test_database_records_upload_failure_for_retry(tmp_path: Path) -> None:
    media = tmp_path / "licensed.mp4"
    media.write_bytes(b"placeholder")
    database = Database(tmp_path / "agent.sqlite3")
    database.initialize()
    source = database.register_source(
        media, RightsRecord("owned", "owner record", "channel-owner")
    )
    clip_id = database.record_clip(source.source_id, 0, 20, tmp_path / "clip.mp4")
    request_id = database.enqueue_publication(clip_id, "A Short", "Description", "private", None)

    database.mark_publication_failed(request_id, "temporary network issue")

    queued_item = database.list_publication_queue()[0]
    assert queued_item["status"] == "failed"
    assert queued_item["upload_error"] == "temporary network issue"