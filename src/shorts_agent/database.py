import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from shorts_agent.models import RightsRecord, SourceAsset


class SourceNotFoundError(LookupError):
    pass


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    rights_basis TEXT NOT NULL,
                    rights_evidence TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS clips (
                    clip_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(source_id),
                    start_seconds REAL NOT NULL,
                    end_seconds REAL NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'rendered',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS publication_queue (
                    request_id TEXT PRIMARY KEY,
                    clip_id TEXT NOT NULL REFERENCES clips(clip_id),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    approved_by TEXT,
                    scheduled_for TEXT,
                    status TEXT NOT NULL,
                    youtube_video_id TEXT,
                    uploaded_at TEXT,
                    upload_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS activity (
                    activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS candidate_approvals (
                    video_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    channel_title TEXT NOT NULL,
                    license_type TEXT NOT NULL,
                    rights_evidence TEXT NOT NULL,
                    attribution_text TEXT NOT NULL DEFAULT '',
                    approved_by TEXT NOT NULL,
                    approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            queue_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(publication_queue)")
            }
            if "scheduled_for" not in queue_columns:
                connection.execute("ALTER TABLE publication_queue ADD COLUMN scheduled_for TEXT")
            for column in ("youtube_video_id", "uploaded_at", "upload_error"):
                if column not in queue_columns:
                    connection.execute(
                        f"ALTER TABLE publication_queue ADD COLUMN {column} TEXT"
                    )
            approval_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(candidate_approvals)")
            }
            if "attribution_text" not in approval_columns:
                connection.execute(
                    "ALTER TABLE candidate_approvals ADD COLUMN attribution_text TEXT NOT NULL DEFAULT ''"
                )

    def approve_candidate(
        self,
        video_id: str,
        title: str,
        url: str,
        channel_title: str,
        license_type: str,
        rights_evidence: str,
        attribution_text: str,
        approved_by: str,
    ) -> None:
        values = (video_id, title, url, channel_title, rights_evidence, attribution_text, approved_by)
        if not all(value.strip() for value in values):
            raise ValueError("Candidate details, rights evidence, attribution, and approver are required")
        if license_type != "creativeCommon":
            raise ValueError("Only candidates returned by the Creative Commons search can be approved here")
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO candidate_approvals
                         (video_id, title, url, channel_title, license_type, rights_evidence,
                          attribution_text, approved_by)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET
                     title = excluded.title,
                     url = excluded.url,
                     channel_title = excluded.channel_title,
                     license_type = excluded.license_type,
                     rights_evidence = excluded.rights_evidence,
                     attribution_text = excluded.attribution_text,
                     approved_by = excluded.approved_by,
                     approved_at = CURRENT_TIMESTAMP""",
                 (
                    video_id,
                    title,
                    url,
                    channel_title,
                    license_type,
                    rights_evidence,
                    attribution_text,
                    approved_by,
                 ),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("candidate_approved_for_clipping", f"{title.strip()} ({approved_by.strip()})"),
            )

    def list_candidate_approvals(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM candidate_approvals ORDER BY approved_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def register_source(self, path: Path, rights: RightsRecord) -> SourceAsset:
        rights.validate()
        resolved_path = path.expanduser().resolve(strict=True)
        if not resolved_path.is_file():
            raise ValueError("Source must be a local file")
        source_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO sources
                   (source_id, path, rights_basis, rights_evidence, confirmed_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (source_id, str(resolved_path), rights.basis, rights.evidence, rights.confirmed_by),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("source_registered", str(resolved_path)),
            )
        return SourceAsset(source_id, resolved_path, rights)

    def get_source(self, source_id: str) -> SourceAsset:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        if row is None:
            raise SourceNotFoundError(source_id)
        rights = RightsRecord(row["rights_basis"], row["rights_evidence"], row["confirmed_by"])
        rights.validate()
        return SourceAsset(row["source_id"], Path(row["path"]), rights)

    def list_sources(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT s.*, COUNT(c.clip_id) AS clip_count
                   FROM sources s LEFT JOIN clips c ON c.source_id = s.source_id
                   GROUP BY s.source_id ORDER BY s.created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def record_clip(
        self, source_id: str, start_seconds: float, end_seconds: float, path: Path
    ) -> str:
        clip_id = str(uuid.uuid4())
        with self.connect() as connection:
            source = connection.execute(
                "SELECT source_id FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise SourceNotFoundError(source_id)
            connection.execute(
                """INSERT INTO clips
                   (clip_id, source_id, start_seconds, end_seconds, path)
                   VALUES (?, ?, ?, ?, ?)""",
                (clip_id, source_id, start_seconds, end_seconds, str(path.resolve())),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("clip_rendered", str(path.resolve())),
            )
        return clip_id

    def list_clips(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.*, s.path AS source_path
                   FROM clips c JOIN sources s ON s.source_id = c.source_id
                   ORDER BY c.created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def enqueue_publication(
        self,
        clip_id: str,
        title: str,
        description: str,
        visibility: str,
        approved_by: str | None,
        scheduled_for: datetime | None = None,
    ) -> str:
        from shorts_agent.models import PublicationRequest

        PublicationRequest(visibility, approved_by, scheduled_for).validate()
        if not title.strip():
            raise ValueError("A title is required")
        request_id = str(uuid.uuid4())
        status = "ready" if visibility == "private" else "approved"
        with self.connect() as connection:
            clip = connection.execute(
                "SELECT clip_id FROM clips WHERE clip_id = ?", (clip_id,)
            ).fetchone()
            if clip is None:
                raise LookupError(f"Clip not found: {clip_id}")
            connection.execute(
                """INSERT INTO publication_queue
                         (request_id, clip_id, title, description, visibility, approved_by, scheduled_for, status)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (
                          request_id,
                          clip_id,
                          title.strip(),
                          description.strip(),
                          visibility,
                          approved_by,
                          scheduled_for.isoformat(timespec="minutes") if scheduled_for else None,
                          status,
                     ),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("publication_queued", f"{title.strip()} ({visibility})"),
            )
        return request_id

    def list_publication_queue(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT q.*, c.path AS clip_path
                   FROM publication_queue q JOIN clips c ON c.clip_id = q.clip_id
                   ORDER BY q.created_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_publication_uploaded(self, request_id: str, youtube_video_id: str) -> None:
        if not youtube_video_id.strip():
            raise ValueError("YouTube video ID is required")
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM publication_queue WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"Publication request not found: {request_id}")
            if row["status"] == "uploaded":
                raise ValueError("This publication request has already been uploaded")
            if row["status"] not in {"ready", "approved", "failed"}:
                raise PermissionError("Publication request is not approved for upload")
            connection.execute(
                """UPDATE publication_queue SET status = 'uploaded', youtube_video_id = ?,
                   uploaded_at = CURRENT_TIMESTAMP, upload_error = NULL WHERE request_id = ?""",
                (youtube_video_id.strip(), request_id),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("youtube_upload_completed", youtube_video_id.strip()),
            )

    def mark_publication_failed(self, request_id: str, error: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM publication_queue WHERE request_id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f"Publication request not found: {request_id}")
            if row["status"] == "uploaded":
                raise ValueError("An uploaded item cannot be marked failed")
            connection.execute(
                "UPDATE publication_queue SET status = 'failed', upload_error = ? WHERE request_id = ?",
                (error[:1000], request_id),
            )
            connection.execute(
                "INSERT INTO activity (event, detail) VALUES (?, ?)",
                ("youtube_upload_failed", f"{request_id}: {error[:200]}"),
            )

    def list_activity(self, limit: int = 12) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM activity ORDER BY activity_id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def metrics(self) -> dict[str, int]:
        with self.connect() as connection:
            sources = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            clips = connection.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
            queued = connection.execute(
                "SELECT COUNT(*) FROM publication_queue WHERE status != 'uploaded'"
            ).fetchone()[0]
        return {"sources": sources, "clips": clips, "queued": queued}