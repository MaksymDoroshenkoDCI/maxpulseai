# YouTube Shorts Trend-to-Publish Agent

A Python 3.11+ starter project for preparing Shorts from media you own or are explicitly licensed to use. Search results are discovery leads only: they do not grant permission to download, edit, or publish a video. A short excerpt, added captions, or other edits do not automatically qualify as fair use.

## Included

- SerpApi YouTube search adapter for trend discovery.
- SQLite source registry that requires a rights basis, evidence reference, and named confirmer.
- Local-file-only source intake; there is intentionally no downloader for arbitrary YouTube URLs.
- FFmpeg 9:16 clip rendering with a 60-second maximum.
- Publication policy that defaults to private and requires a named human approver for public or scheduled visibility.
- Provider protocols for transcription, clip selection, metadata generation, and YouTube upload.

The transcription, LLM, subtitle-generation, OAuth, and upload providers are extension points, not implemented integrations. The CLI does not publish content. Review generated clips, rights, and metadata before connecting a publisher.

## Setup

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,dashboard,youtube]'
```

Install FFmpeg separately to render clips. Configure `SERPAPI_API_KEY` for general trend search and `YOUTUBE_DATA_API_KEY` for YouTube Creative Commons-filtered search in a local `.env` file. OAuth settings may also be set in `.env`: `YOUTUBE_OAUTH_CLIENT_SECRETS` (defaults to `.secrets/youtube-oauth-client.json`), `YOUTUBE_TOKEN_FILE` (defaults to ignored `data/youtube-token.json`), and `YOUTUBE_CHANNEL_ID` (defaults to the configured channel). `RIGHTS_REVIEWER_NAME` can prefill the responsible reviewer's name. Never commit OAuth client JSON or token files.

## Streamlit dashboard

Run the local dashboard from the project root:

```sh
streamlit run src/shorts_agent/dashboard.py
```

The dashboard provides an overview, SerpApi trend search, YouTube Data API search filtered to videos marked Creative Commons, authorized local media intake, clip rendering, and a publication review queue. On each Creative Commons result, the responsible person can record license evidence, the required attribution text, and press **Погоджено до нарізки** after checking the video's actual license and terms. The approval, evidence, attribution, reviewer, and timestamp are stored in SQLite. A YouTube license marker is only a search filter, not a legal verification. This approval records the decision but does not download or cut the YouTube video; downloading and clipping search results are not implemented.

### Connect YouTube uploads

1. In Google Cloud, enable YouTube Data API v3, configure the OAuth consent screen, add your Google account as a test user, and create an OAuth client of type **Desktop app**.
2. Download its JSON into `.secrets/youtube-oauth-client.json` (or set `YOUTUBE_OAUTH_CLIENT_SECRETS` to its local path). This file is ignored by Git.
3. Set `YOUTUBE_CHANNEL_ID` to the channel ID to protect against uploading to the wrong channel. The default is the channel previously provided for this workspace.
4. Install the `youtube` extra above, start the dashboard, open **Review queue**, and click **Connect / verify channel**. Google sign-in opens in a browser. The app requests upload permission plus read-only channel access so it can verify the exact channel ID before saving the token.
5. Prepare a queue item, review title/description/visibility, then click **Upload to my YouTube channel**. The upload is resumable and its YouTube video ID/status are stored in SQLite. Failed requests can be retried; successfully uploaded requests are locked against duplicate uploads. Use **Switch Google account** if the OAuth client selected a different account/channel.

Private is the default. Public items require a named approver when added to the queue. Scheduled items also require a named approver and future local date/time; they are uploaded with private visibility and YouTube's `publishAt` timestamp. Transcription/LLM providers are not implemented. Store credentials only in ignored local files; never commit OAuth client JSON or tokens.

## Use

Search candidates from a niche without downloading them:

```sh
python -c 'from shorts_agent.discovery import SerpApiDiscovery; print(SerpApiDiscovery().search("science podcast", limit=5))'
```

Register a local file after confirming you have the relevant rights:

```sh
shorts-agent source-add ./media/authorized-video.mp4 \
  --rights-basis "written permission" \
  --rights-evidence "permission-record-2026-01" \
  --confirmed-by "channel-owner"
```

Render a clip using its printed source ID:

```sh
shorts-agent render SOURCE_ID --start 42 --end 87 --output ./renders/clip.mp4
```

Publication policy checks are explicit; private is the default:

```sh
shorts-agent check-publication
shorts-agent check-publication --visibility public --approved-by "channel-owner"
```

## Tests

```sh
pytest
```

Tests run offline and do not require API keys, FFmpeg, or YouTube credentials.

## Next integration steps

Implement provider adapters behind `providers.py`: Whisper or another transcription service, an LLM constrained to timestamped transcript segments, and subtitle rendering. Keep the human approval check in the publish path, retain evidence and upload logs, and use YouTube's current API quota and policy documentation when deploying. Do not treat transformations or clip length as a rights exemption.
