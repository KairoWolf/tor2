"""Video compression and validation via ffmpeg/ffprobe."""

import json
import shutil
import subprocess
from pathlib import Path

MAX_SOURCE_BYTES = 500 * 1024 * 1024  # refuse to even read sources above this
MAX_DURATION_S = 10 * 60


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path: Path) -> dict:
    """Return {'duration': float, 'codec': str}; raises ValueError if not a video."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ValueError("ffmpeg/ffprobe is not installed") from None
    if out.returncode != 0:
        raise ValueError("ffprobe could not read this file as video")
    info = json.loads(out.stdout)
    streams = info.get("streams") or []
    if not streams:
        raise ValueError("no video stream found")
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    return {"duration": duration, "codec": streams[0].get("codec_name", "?")}


def compress(src: Path, dest: Path) -> None:
    """Blocking: transcode to a small H.264/AAC mp4 (≤480p, crf 28)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(src),
             "-vf", "scale='min(854,iw)':-2",
             "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
             "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k",
             "-movflags", "+faststart",
             str(dest)],
            capture_output=True, text=True, timeout=15 * 60)
    except FileNotFoundError:
        raise ValueError("ffmpeg is not installed") from None
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise ValueError(f"ffmpeg failed: {tail}")


def validate_received(path: Path) -> None:
    """Confirm received bytes actually decode as a video before keeping them."""
    probe(path)
