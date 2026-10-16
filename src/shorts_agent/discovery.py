import json
import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import urlopen

import certifi


def _open_json(url: str) -> dict[str, object]:
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(url, timeout=20, context=context) as response:
        return json.load(response)


@dataclass(frozen=True)
class VideoCandidate:
    video_id: str
    title: str
    url: str
    views: str | None = None
    channel_title: str = ""
    license_type: str = "unverified"


class SerpApiDiscovery:
    """Search public YouTube metadata; results are leads, never authorized source assets."""

    endpoint = "https://serpapi.com/search.json"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")
        if not self.api_key:
            raise ValueError("SERPAPI_API_KEY is required")

    def search(self, query: str, limit: int = 10) -> list[VideoCandidate]:
        params = urlencode({"engine": "youtube", "search_query": query, "api_key": self.api_key})
        payload = _open_json(f"{self.endpoint}?{params}")
        results = payload.get("video_results", [])[:limit]
        return [
            VideoCandidate(
                video_id=item.get("id", ""),
                title=item.get("title", ""),
                url=item.get("link", ""),
                views=item.get("views"),
                channel_title=item.get("channel", {}).get("name", ""),
            )
            for item in results
        ]


class YouTubeCreativeCommonsDiscovery:
    """Search YouTube Data API results filtered to videos marked Creative Commons."""

    endpoint = "https://www.googleapis.com/youtube/v3/search"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("YOUTUBE_DATA_API_KEY")
        if not self.api_key:
            raise ValueError("YOUTUBE_DATA_API_KEY is required")

    def search(self, query: str, limit: int = 10) -> list[VideoCandidate]:
        params = urlencode(
            {
                "part": "snippet",
                "q": query,
                "type": "video",
                "videoLicense": "creativeCommon",
                "order": "viewCount",
                "maxResults": min(max(limit, 1), 50),
                "key": self.api_key,
            }
        )
        payload = _open_json(f"{self.endpoint}?{params}")
        return [
            VideoCandidate(
                video_id=item["id"]["videoId"],
                title=item["snippet"]["title"],
                url=f"https://www.youtube.com/watch?v={item['id']['videoId']}",
                channel_title=item["snippet"].get("channelTitle", ""),
                license_type="creativeCommon",
            )
            for item in payload.get("items", [])
            if item.get("id", {}).get("videoId")
        ]