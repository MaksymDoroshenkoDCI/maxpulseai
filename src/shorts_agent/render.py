import shutil
import subprocess
from pathlib import Path

from shorts_agent.models import ClipSegment, SourceAsset


def render_vertical_clip(
    source: SourceAsset,
    segment: ClipSegment,
    output: Path,
    ffmpeg_path: str = "ffmpeg",
) -> Path:
    source.rights.validate()
    segment.validate()
    executable = shutil.which(ffmpeg_path)
    if executable is None:
        raise RuntimeError("FFmpeg was not found; install it and ensure it is on PATH")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "-y",
        "-ss",
        str(segment.start_seconds),
        "-i",
        str(source.path),
        "-t",
        str(segment.end_seconds - segment.start_seconds),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)
    return output