from pathlib import Path
from typing import Protocol

from shorts_agent.models import ClipSegment, PublicationRequest, SourceAsset


class Transcriber(Protocol):
    def transcribe(self, source: SourceAsset) -> str: ...


class ClipSelector(Protocol):
    def select(self, transcript: str) -> list[ClipSegment]: ...


class MetadataGenerator(Protocol):
    def generate(self, transcript: str) -> tuple[str, str]: ...


class YouTubePublisher(Protocol):
    def upload(self, video: Path, title: str, description: str, request: PublicationRequest) -> str: ...