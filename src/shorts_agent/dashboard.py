import os
import shutil
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import streamlit as st

from shorts_agent.database import Database
from shorts_agent.discovery import (
    SerpApiDiscovery,
    VideoCandidate,
    YouTubeCreativeCommonsDiscovery,
)
from shorts_agent.models import ClipSegment, RightsRecord
from shorts_agent.render import render_vertical_clip
from shorts_agent.models import PublicationRequest
from shorts_agent.youtube import get_authorized_youtube_service, upload_video


st.set_page_config(
    page_title="Shortform Studio",
    page_icon="S",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;600;700&display=swap');
    :root { --ink: #142923; --muted: #71817b; --paper: #f4f6f2; --line: #dfe6dd;
            --green: #1d654d; --lime: #d5ed68; --orange: #e58754; }
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    .stApp { background: var(--paper); color: var(--ink); }
    [data-testid="stHeader"] { background: rgba(244,246,242,.88); }
    [data-testid="stSidebar"] { background: #e9eee7; border-right: 1px solid var(--line); }
    [data-testid="stSidebar"], [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3, [data-testid="stSidebar"] strong,
    [data-testid="stSidebar"] code { color: var(--ink) !important; }
    [data-testid="stSidebar"] code { background: transparent !important; }
    h1, h2, h3 { font-family: 'Space Grotesk', sans-serif !important; color: var(--ink); }
    h1 { letter-spacing: 0 !important; font-size: 2.1rem !important; }
    [data-testid="stMetric"] { background: #fff; padding: 18px 20px; border: 1px solid var(--line); border-radius: 8px; }
    [data-testid="stMetricLabel"] { color: var(--muted); }
    [data-testid="stMetricValue"] { font-family: 'Space Grotesk', sans-serif; color: var(--ink); }
    [data-testid="stTabs"] button,
    [data-testid="stTabs"] button p { color: #000 !important; font-weight: 600; }
    div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"] {
      background: var(--green); border: 0; color: white; border-radius: 6px; font-weight: 700;
    }
    div.stButton > button[kind="primary"]:hover, div.stFormSubmitButton > button[kind="primary"]:hover {
      background: #154c39; color: white; border: 0;
    }
    .studio-kicker { color: var(--green); text-transform: uppercase; font-weight: 700; font-size: .72rem; letter-spacing: .08em; }
    .studio-subtitle { color: var(--muted); margin-top: -.7rem; }
    .status-strip { border-left: 4px solid var(--lime); background: #fff; padding: 13px 16px; border-radius: 0 6px 6px 0; margin: 10px 0 20px; }
    .small-muted { color: var(--muted); font-size: .88rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def get_database() -> Database:
    path = Path(os.getenv("SHORTS_AGENT_DATABASE") or "data/shorts-agent.sqlite3")
    database = Database(path)
    database.initialize()
    return database


def show_table(rows: list[dict[str, object]], empty_message: str) -> None:
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info(empty_message)


def render_overview(database: Database) -> None:
    metrics = database.metrics()
    metric_columns = st.columns(3)
    metric_columns[0].metric("Authorized sources", metrics["sources"])
    metric_columns[1].metric("Rendered clips", metrics["clips"])
    metric_columns[2].metric("Review queue", metrics["queued"])

    left, right = st.columns([1.4, 1], gap="large")
    with left:
        st.subheader("Recent activity")
        events = database.list_activity(10)
        if events:
            display_events = [
                {
                    "When": item["created_at"],
                    "Event": str(item["event"]).replace("_", " ").title(),
                    "Details": item["detail"],
                }
                for item in events
            ]
            st.dataframe(display_events, use_container_width=True, hide_index=True)
        else:
            st.info("Your workflow activity will appear here.")
    with right:
        st.subheader("Pipeline health")
        serpapi_ready = bool(os.getenv("SERPAPI_API_KEY"))
        ffmpeg_ready = shutil.which("ffmpeg") is not None
        oauth_client = Path(
            os.getenv("YOUTUBE_OAUTH_CLIENT_SECRETS")
            or ".secrets/youtube-oauth-client.json"
        )
        st.markdown(
            f"{'🟢' if serpapi_ready else '⚪'} SerpApi search {'connected' if serpapi_ready else 'not configured'}"
        )
        st.markdown(f"{'🟢' if ffmpeg_ready else '🟠'} FFmpeg {'ready' if ffmpeg_ready else 'not found'}")
        st.markdown(
            f"{'🟢' if oauth_client.is_file() else '⚪'} YouTube OAuth "
            f"{'client configured' if oauth_client.is_file() else 'client not configured'}"
        )
        st.caption("Transcription and AI clip selection are not configured yet.")


def render_discovery(database: Database) -> None:
    st.subheader("Trend search")
    st.caption("General trend results are for discovery only; they are not permission to reuse a video.")
    with st.form("discovery-form"):
        query_column, limit_column = st.columns([3, 1])
        query = query_column.text_input("Niche or search phrase", placeholder="science podcast clips")
        limit = limit_column.number_input("Results", min_value=1, max_value=20, value=10)
        api_key = st.text_input(
            "SerpApi key",
            value=os.getenv("SERPAPI_API_KEY", ""),
            type="password",
            help="Read from SERPAPI_API_KEY when configured in the environment or local .env.",
        )
        search_clicked = st.form_submit_button("Search YouTube", type="primary")

    if search_clicked:
        if not query.strip():
            st.warning("Enter a search phrase first.")
        else:
            try:
                results = SerpApiDiscovery(api_key=api_key).search(query.strip(), int(limit))
                st.session_state["discovery_results"] = results
                st.session_state["discovery_query"] = query.strip()
            except Exception as error:
                st.error(f"Search failed: {error}")

    candidates: list[VideoCandidate] = st.session_state.get("discovery_results", [])
    if candidates:
        st.caption(f"Results for: {st.session_state.get('discovery_query', '')}")
        st.dataframe(
            [
                {"Title": item.title, "Views": item.views or "Not provided", "Open": item.url}
                for item in candidates
            ],
            use_container_width=True,
            hide_index=True,
            column_config={"Open": st.column_config.LinkColumn("Open video")},
        )
        st.warning("Only add a local file in Sources after confirming ownership or explicit permission.")

    st.divider()
    st.subheader("Creative Commons candidates")
    st.caption(
        "Search YouTube videos marked Creative Commons. The license filter is a lead, not legal verification; "
        "review the video's license details and terms before approving it."
    )
    with st.form("creative-commons-search-form"):
        cc_query_column, cc_limit_column = st.columns([3, 1])
        cc_query = cc_query_column.text_input(
            "Search licensed videos", placeholder="topic, creator, or channel"
        )
        cc_limit = cc_limit_column.number_input(
            "Maximum results", min_value=1, max_value=20, value=10, key="cc_result_limit"
        )
        youtube_key = st.text_input(
            "YouTube Data API key",
            value=os.getenv("YOUTUBE_DATA_API_KEY", ""),
            type="password",
            help="Use YOUTUBE_DATA_API_KEY from your local .env or environment.",
        )
        cc_search_clicked = st.form_submit_button("Search Creative Commons", type="primary")

    if cc_search_clicked:
        if not cc_query.strip():
            st.warning("Enter a search phrase first.")
        else:
            try:
                candidates = YouTubeCreativeCommonsDiscovery(api_key=youtube_key).search(
                    cc_query.strip(), int(cc_limit)
                )
                st.session_state["cc_candidates"] = candidates
                st.session_state["cc_query"] = cc_query.strip()
            except Exception as error:
                st.error(f"Creative Commons search failed: {error}")

    cc_candidates: list[VideoCandidate] = st.session_state.get("cc_candidates", [])
    if cc_candidates:
        st.caption(f"Filtered results for: {st.session_state.get('cc_query', '')}")
        approvals = {
            str(approval["video_id"]): approval
            for approval in database.list_candidate_approvals()
        }
        for candidate in cc_candidates:
            approved = candidate.video_id in approvals
            with st.container(border=True):
                title_column, status_column = st.columns([4, 1])
                title_column.markdown(f"**[{candidate.title}]({candidate.url})**")
                title_column.caption(
                    f"{candidate.channel_title or 'Unknown channel'} · "
                    f"{candidate.views or 'Views not provided'} · YouTube marks as Creative Commons"
                )
                status_column.success("Approved") if approved else status_column.info("Needs review")
                if approved:
                    approval = approvals[candidate.video_id]
                    st.caption(
                        f"Approved by {approval['approved_by']} on {approval['approved_at']}; "
                        "license verification evidence is recorded."
                    )
                else:
                    with st.form(f"approve-cc-{candidate.video_id}"):
                        reviewer = st.text_input(
                            "Responsible reviewer",
                            value=os.getenv("RIGHTS_REVIEWER_NAME", ""),
                            key=f"reviewer-{candidate.video_id}",
                        )
                        evidence = st.text_input(
                            "License evidence / notes",
                            value=candidate.url,
                            key=f"evidence-{candidate.video_id}",
                            help="Record where you verified the license and any attribution or reuse terms.",
                        )
                        attribution = st.text_area(
                            "Attribution to include with the clip",
                            value=f'{candidate.title} by {candidate.channel_title} — {candidate.url}',
                            key=f"attribution-{candidate.video_id}",
                            help="Creative Commons reuse requires attribution; adjust this to match the license terms.",
                        )
                        license_checked = st.checkbox(
                            "I personally checked the video's license and reuse terms",
                            key=f"license-check-{candidate.video_id}",
                        )
                        approve_clicked = st.form_submit_button(
                            "Погоджено до нарізки", type="primary"
                        )
                    if approve_clicked:
                        if not license_checked:
                            st.error("Review and confirm the video's license terms before approval.")
                        else:
                            try:
                                database.approve_candidate(
                                    video_id=candidate.video_id,
                                    title=candidate.title,
                                    url=candidate.url,
                                    channel_title=candidate.channel_title or "Unknown channel",
                                    license_type=candidate.license_type,
                                    rights_evidence=evidence,
                                    attribution_text=attribution,
                                    approved_by=reviewer,
                                )
                                st.success(
                                    "Approval saved. This records your decision; downloading and clipping "
                                    "the YouTube video are not enabled yet."
                                )
                            except (ValueError, sqlite3.Error) as error:
                                st.error(str(error))

    saved_approvals = database.list_candidate_approvals()
    if saved_approvals:
        st.divider()
        st.subheader("Approved for clipping review")
        show_table(
            [
                {
                    "Title": approval["title"],
                    "Channel": approval["channel_title"],
                    "License marker": approval["license_type"],
                        "Attribution": approval["attribution_text"],
                    "Approved by": approval["approved_by"],
                    "Evidence": approval["rights_evidence"],
                    "Approved at": approval["approved_at"],
                    "Video": approval["url"],
                }
                for approval in saved_approvals
            ],
            "No reviewed candidates yet.",
        )


def render_sources(database: Database) -> None:
    st.subheader("Authorized media library")
    st.caption("Files stay on this machine. Add only content you own or have documented permission to use.")
    with st.form("source-form", clear_on_submit=True):
        media_file = st.file_uploader("Choose a video file", type=["mp4", "mov", "mkv", "webm"])
        rights_basis = st.selectbox(
            "Rights basis",
            ["Owned by my channel", "Written permission", "Creative Commons license", "Other documented license"],
        )
        evidence = st.text_input("Evidence reference", placeholder="Contract ID, license URL, or internal record")
        confirmed_by = st.text_input("Confirmed by", placeholder="Name of rights reviewer")
        confirmed = st.checkbox("I confirm the evidence covers editing and publishing this source")
        add_clicked = st.form_submit_button("Add to library", type="primary")

    if add_clicked:
        if media_file is None:
            st.error("Choose a local video file.")
        elif not confirmed or not evidence.strip() or not confirmed_by.strip():
            st.error("Confirm the rights declaration and provide both evidence and reviewer name.")
        elif media_file.size > 200 * 1024 * 1024:
            st.error("This dashboard currently accepts files up to 200 MB.")
        else:
            suffix = Path(media_file.name).suffix.lower()
            asset_path = Path("data/sources") / f"{uuid.uuid4().hex}{suffix}"
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(media_file.getvalue())
            try:
                source = database.register_source(
                    asset_path,
                    RightsRecord(rights_basis, evidence.strip(), confirmed_by.strip()),
                )
                st.success(f"Source added: {source.source_id}")
            except (OSError, ValueError, sqlite3.Error) as error:
                asset_path.unlink(missing_ok=True)
                st.error(f"Could not add source: {error}")

    source_rows = database.list_sources()
    display_rows = [
        {
            "Added": row["created_at"],
            "File": Path(str(row["path"])).name,
            "Clips": row["clip_count"],
            "Rights basis": row["rights_basis"],
            "Evidence": row["rights_evidence"],
            "Confirmed by": row["confirmed_by"],
            "Source ID": row["source_id"],
        }
        for row in source_rows
    ]
    show_table(display_rows, "No authorized sources yet. Add a licensed or owned video above.")


def render_clip_studio(database: Database) -> None:
    source_rows = database.list_sources()
    if not source_rows:
        st.info("Add an authorized local video in Sources before rendering a clip.")
        return

    source_options = {f"{Path(str(row['path'])).name} · {row['source_id'][:8]}": row for row in source_rows}
    with st.form("render-form"):
        st.subheader("Create a vertical clip")
        selected_label = st.selectbox("Source video", list(source_options))
        start_column, end_column = st.columns(2)
        start_seconds = start_column.number_input("Start (seconds)", min_value=0.0, value=0.0, step=1.0)
        end_seconds = end_column.number_input("End (seconds)", min_value=1.0, max_value=60.0, value=45.0, step=1.0)
        render_clicked = st.form_submit_button("Render 9:16 clip", type="primary")

    if render_clicked:
        source_row = source_options[selected_label]
        source_id = str(source_row["source_id"])
        clip_path = Path("data/renders") / f"{uuid.uuid4().hex}.mp4"
        try:
            source = database.get_source(source_id)
            render_vertical_clip(source, ClipSegment(start_seconds, end_seconds), clip_path)
            database.record_clip(source_id, start_seconds, end_seconds, clip_path)
            st.success(f"Rendered clip saved to {clip_path}")
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            st.error(f"Render failed: {error}")

    clips = database.list_clips()
    if clips:
        st.divider()
        st.subheader("Rendered clips")
        clip_options = {
            f"{Path(str(clip['path'])).name} · {str(clip['clip_id'])[:8]}": clip for clip in clips
        }
        selected_clip = st.selectbox("Preview clip", list(clip_options))
        clip = clip_options[selected_clip]
        preview_path = Path(str(clip["path"]))
        if preview_path.is_file():
            st.video(preview_path.read_bytes())
        st.caption(
            f"{float(clip['start_seconds']):.1f}s to {float(clip['end_seconds']):.1f}s · "
            f"Source: {Path(str(clip['source_path'])).name}"
        )


def render_review_queue(database: Database) -> None:
    st.subheader("YouTube channel")
    client_secrets = Path(
        os.getenv("YOUTUBE_OAUTH_CLIENT_SECRETS") or ".secrets/youtube-oauth-client.json"
    )
    token_file = Path(os.getenv("YOUTUBE_TOKEN_FILE") or "data/youtube-token.json")
    expected_channel_id = os.getenv(
        "YOUTUBE_CHANNEL_ID", "UCuofIedUoXyGTuyL0QA-DsQ"
    )
    st.caption(f"Target channel: {expected_channel_id}")
    connect_column, switch_column, config_column = st.columns([1, 1, 2])
    connect_clicked = connect_column.button("Connect / verify channel", type="primary")
    switch_clicked = switch_column.button(
        "Switch Google account", disabled=not token_file.is_file()
    )
    config_column.caption(f"OAuth client file: {client_secrets}")
    if connect_clicked or switch_clicked:
        try:
            _, channel_title = get_authorized_youtube_service(
                client_secrets,
                token_file,
                expected_channel_id,
                force_reauthorize=switch_clicked,
            )
            st.session_state["youtube_connected_channel"] = channel_title
            st.success(f"Connected and verified channel: {channel_title} ({expected_channel_id})")
        except Exception as error:
            st.error(f"Could not connect YouTube: {error}")

    connected_channel = st.session_state.get("youtube_connected_channel")
    if connected_channel:
        st.success(f"Verified channel: {connected_channel} · {expected_channel_id}")
    elif token_file.is_file():
        st.info("OAuth token found. Use Connect / verify channel to confirm the selected account.")
    else:
        st.info("Connect with Google before uploading. The OAuth client JSON must be configured locally.")

    clips = database.list_clips()
    if not clips:
        st.info("Render a clip before preparing a publication request.")
        return

    st.subheader("Prepare publication metadata")
    st.caption("Each upload requires a deliberate click. Private is the default visibility.")
    clip_options = {
        f"{Path(str(clip['path'])).name} · {str(clip['clip_id'])[:8]}": clip for clip in clips
    }
    selected_label = st.selectbox("Rendered clip", list(clip_options))
    visibility = st.selectbox("Visibility", ["private", "public", "scheduled"], index=0)
    with st.form("queue-form"):
        title = st.text_input("Title", max_chars=100)
        description = st.text_area("Description", height=100)
        approved_by = None
        if visibility != "private":
            approved_by = st.text_input("Human approver", placeholder="Required for public or scheduled")
        schedule_date = None
        schedule_time = None
        if visibility == "scheduled":
            schedule_date = st.date_input("Schedule date", value=date.today() + timedelta(days=1))
            schedule_time = st.time_input("Schedule time", value=time(12, 0))
            st.caption("Schedule time uses the local timezone of this machine.")
        queue_clicked = st.form_submit_button("Add to review queue", type="primary")

    if queue_clicked:
        clip = clip_options[selected_label]
        try:
            scheduled_for = (
                datetime.combine(schedule_date, schedule_time) if visibility == "scheduled" else None
            )
            request_id = database.enqueue_publication(
                str(clip["clip_id"]),
                title,
                description,
                visibility,
                approved_by,
                scheduled_for,
            )
            st.success(f"Request added to queue: {request_id}")
        except (ValueError, PermissionError, LookupError, sqlite3.Error) as error:
            st.error(str(error))

    queue_rows = database.list_publication_queue()
    if not queue_rows:
        st.info("The publication queue is empty.")
    for row in queue_rows:
        with st.container(border=True):
            st.markdown(f"**{row['title']}** · {row['status']}")
            st.caption(
                f"{Path(str(row['clip_path'])).name} · {row['visibility']} · "
                f"Approved by: {row['approved_by'] or 'you at upload time'}"
            )
            if row["scheduled_for"]:
                st.caption(f"Scheduled for local time: {row['scheduled_for']}")
            if row["status"] == "uploaded" and row["youtube_video_id"]:
                st.success("Upload complete")
                st.link_button(
                    "Open YouTube video",
                    f"https://www.youtube.com/watch?v={row['youtube_video_id']}",
                )
                st.caption(f"Uploaded at: {row['uploaded_at']}")
            elif row["upload_error"]:
                st.error(f"Previous upload failed: {row['upload_error']}")

            if row["status"] in {"ready", "approved", "failed"}:
                video_path = Path(str(row["clip_path"]))
                if not video_path.is_file():
                    st.error("The rendered video file is missing; upload is disabled.")
                    continue
                if row["visibility"] in {"public", "scheduled"}:
                    st.warning(
                        f"This will publish as {row['visibility']}. Approval recorded by: {row['approved_by']}."
                    )
                upload_clicked = st.button(
                    "Upload to my YouTube channel",
                    key=f"upload-{row['request_id']}",
                    type="primary",
                    disabled=not client_secrets.is_file(),
                )
                if not client_secrets.is_file():
                    st.caption("Add your OAuth Desktop client JSON at the configured path to enable upload.")
                if upload_clicked:
                    publication = PublicationRequest(
                        str(row["visibility"]),
                        str(row["approved_by"]) if row["approved_by"] else None,
                        datetime.fromisoformat(str(row["scheduled_for"]))
                        if row["scheduled_for"]
                        else None,
                    )
                    try:
                        service, channel_title = get_authorized_youtube_service(
                            client_secrets, token_file, expected_channel_id
                        )
                        progress_bar = st.progress(0.0, text="Uploading to YouTube...")
                        youtube_video_id = upload_video(
                            service,
                            video_path,
                            str(row["title"]),
                            str(row["description"]),
                            publication,
                            progress_callback=lambda value: progress_bar.progress(
                                min(value, 1.0), text="Uploading to YouTube..."
                            ),
                        )
                        database.mark_publication_uploaded(
                            str(row["request_id"]), youtube_video_id
                        )
                        st.success(f"Uploaded to {channel_title}: {youtube_video_id}")
                        st.link_button(
                            "Open uploaded video",
                            f"https://www.youtube.com/watch?v={youtube_video_id}",
                        )
                    except Exception as error:
                        try:
                            database.mark_publication_failed(str(row["request_id"]), str(error))
                        except (LookupError, ValueError, sqlite3.Error):
                            pass
                        st.error(f"Upload failed: {error}")


def main() -> None:
    database = get_database()
    with st.sidebar:
        st.markdown("### SHORTFORM STUDIO")
        st.caption("Trend-to-clip workspace")
        st.divider()
        st.markdown("**Database**")
        st.caption(str(database.path))
        st.markdown(f"**SerpApi**  \n{'Connected' if os.getenv('SERPAPI_API_KEY') else 'Needs key'}")
        st.markdown(
            f"**YouTube CC search**  \n"
            f"{'Connected' if os.getenv('YOUTUBE_DATA_API_KEY') else 'Needs key'}"
        )
        st.markdown(f"**FFmpeg**  \n{'Ready' if shutil.which('ffmpeg') else 'Not installed'}")
        st.divider()
        st.caption("Local media only. Rights evidence is required before processing.")

    st.markdown('<div class="studio-kicker">Creator operations · Local workspace</div>', unsafe_allow_html=True)
    st.title("Shortform Studio")
    st.markdown(
        '<p class="studio-subtitle">Discover candidates, process authorized media, and review every clip before publishing.</p>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="status-strip"><strong>Publishing is guarded.</strong> '
        'Private is the default; public or scheduled requests need a named approver. '
        'Every upload verifies the configured YouTube channel and requires a deliberate click.</div>',
        unsafe_allow_html=True,
    )

    overview_tab, discover_tab, sources_tab, studio_tab, queue_tab = st.tabs(
        ["Overview", "Discover", "Sources", "Clip studio", "Review queue"]
    )
    with overview_tab:
        render_overview(database)
    with discover_tab:
        render_discovery(database)
    with sources_tab:
        render_sources(database)
    with studio_tab:
        render_clip_studio(database)
    with queue_tab:
        render_review_queue(database)


if __name__ == "__main__":
    main()