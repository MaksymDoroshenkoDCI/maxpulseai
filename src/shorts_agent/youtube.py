import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from shorts_agent.models import PublicationRequest

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class ChannelMismatchError(PermissionError):
    pass


def verify_target_channel(service: object, expected_channel_id: str) -> tuple[str, str]:
    if not expected_channel_id.strip():
        raise ValueError("YOUTUBE_CHANNEL_ID must be configured before connecting YouTube")
    response = service.channels().list(part="id,snippet", mine=True).execute()
    channels = response.get("items", [])
    if not channels:
        raise PermissionError("The authorized Google account has no accessible YouTube channel")
    channel = next(
        (item for item in channels if item.get("id") == expected_channel_id), None
    )
    if channel is None:
        available_ids = ", ".join(item.get("id", "unknown") for item in channels)
        raise ChannelMismatchError(
            f"Configured target channel was not found among authorized channels ({available_ids})"
        )
    actual_id = channel["id"]
    channel_title = channel.get("snippet", {}).get("title", "")
    return actual_id, channel_title


def get_authorized_youtube_service(
    client_secrets_file: Path,
    token_file: Path,
    expected_channel_id: str,
    force_reauthorize: bool = False,
) -> tuple[object, str]:
    if not client_secrets_file.is_file():
        raise FileNotFoundError(
            f"OAuth Desktop client JSON not found: {client_secrets_file}"
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    credentials = None
    if token_file.is_file() and not force_reauthorize:
        credentials = Credentials.from_authorized_user_file(
            str(token_file), YOUTUBE_SCOPES
        )
        if not credentials.has_scopes(YOUTUBE_SCOPES):
            credentials = None
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_file), YOUTUBE_SCOPES
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            access_type="offline",
            prompt="consent",
        )

    service = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    _, channel_title = verify_target_channel(service, expected_channel_id)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=token_file.parent, delete=False
    ) as token_temp:
        token_temp.write(credentials.to_json())
        temp_path = Path(token_temp.name)
    try:
        os.chmod(temp_path, 0o600)
        temp_path.replace(token_file)
    finally:
        temp_path.unlink(missing_ok=True)

    return service, channel_title


def build_video_resource(
    title: str,
    description: str,
    publication: PublicationRequest,
) -> dict[str, object]:
    publication.validate()
    if not title.strip():
        raise ValueError("A title is required")
    if len(title.strip()) > 100:
        raise ValueError("YouTube titles must not exceed 100 characters")
    if len(description) > 5000:
        raise ValueError("YouTube descriptions must not exceed 5000 characters")

    status: dict[str, object] = {
        "privacyStatus": "private" if publication.visibility == "scheduled" else publication.visibility
    }
    if publication.visibility == "scheduled":
        scheduled_for = publication.scheduled_for
        if scheduled_for is None:
            raise ValueError("Scheduled publication requires a date and time")
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.astimezone()
        status["publishAt"] = scheduled_for.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "snippet": {
            "title": title.strip(),
            "description": description.strip(),
            "categoryId": "22",
        },
        "status": status,
    }


def upload_video(
    service: object,
    video_file: Path,
    title: str,
    description: str,
    publication: PublicationRequest,
    progress_callback: Callable[[float], None] | None = None,
) -> str:
    video_file = video_file.expanduser().resolve(strict=True)
    if not video_file.is_file():
        raise ValueError("Upload target must be a local video file")

    from googleapiclient.http import MediaFileUpload

    request = service.videos().insert(
        part="snippet,status",
        body=build_video_resource(title, description, publication),
        media_body=MediaFileUpload(
            str(video_file), mimetype="video/*", chunksize=8 * 1024 * 1024, resumable=True
        ),
    )
    response = None
    while response is None:
        progress, response = request.next_chunk()
        if progress_callback and progress is not None:
            progress_callback(progress.progress())
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError("YouTube upload completed without returning a video ID")
    return str(video_id)