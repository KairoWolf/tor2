"""Video compression, thumbnails and validation via ffmpeg/ffprobe."""

import json
import re
import shutil
import subprocess
from pathlib import Path

MAX_SOURCE_BYTES = 500 * 1024 * 1024      # /vid — refuse larger sources
MAX_BIG_SOURCE_BYTES = 3 * 1024 * 1024 * 1024   # /big-vid
MAX_DURATION_S = 10 * 60                  # /vid only; /big-vid has no limit

THUMB_WIDTH = 480
MAX_THUMB_BYTES = 120 * 1024

_TIME_RE = re.compile(r"out_time_ms=(\d+)")


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


def compress(src: Path, dest: Path, on_progress=None, duration: float = 0.0) -> None:
    """Blocking: transcode to a small H.264/AAC mp4 (≤480p, crf 28).

    `on_progress(fraction)` is called as encoding proceeds when `duration` is
    known — ffmpeg reports elapsed output time on the -progress stream.
    """
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-vf", "scale='min(854,iw)':-2",
           "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
           "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "96k",
           "-movflags", "+faststart"]
    if on_progress and duration > 0:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(dest))

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise ValueError("ffmpeg is not installed") from None

    if on_progress and duration > 0:
        for line in proc.stdout:
            m = _TIME_RE.search(line)
            if m:
                done = int(m.group(1)) / 1_000_000 / duration
                on_progress(min(0.999, max(0.0, done)))
    stderr = proc.communicate()[1]
    if proc.returncode != 0:
        tail = (stderr or "").strip().splitlines()
        raise ValueError(f"ffmpeg failed: {tail[-1] if tail else 'unknown error'}")
    if on_progress:
        on_progress(1.0)


def thumbnail(path: Path, at_second: float = 1.0) -> bytes | None:
    """A single JPEG frame, for previewing a video without downloading it."""
    if not have_ffmpeg():
        return None
    for seek in (at_second, 0.0):     # very short clips have no frame at 1s
        try:
            out = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", str(seek), "-i", str(path),
                 "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2",
                 "-q:v", "6", "-f", "image2", "pipe:1"],
                capture_output=True, timeout=120)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if out.returncode == 0 and 0 < len(out.stdout) <= MAX_THUMB_BYTES:
            return out.stdout
    return None


def validate_received(path: Path) -> None:
    """Confirm received bytes actually decode as a video before keeping them."""
    probe(path)


AUDIO_EXTS = {"mp3", "m4a", "aac", "ogg", "opus", "flac", "wav", "wma"}
MAX_AUDIO_BYTES = 200 * 1024 * 1024
AUDIO_BITRATE = "96k"


def probe_audio(path: Path) -> dict:
    """Return {'duration': float, 'codec': str} for a file with audio."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ValueError("ffmpeg/ffprobe is not installed") from None
    if out.returncode != 0:
        raise ValueError("ffprobe could not read this file")
    info = json.loads(out.stdout)
    streams = info.get("streams") or []
    if not streams:
        raise ValueError("no audio stream found")
    return {"duration": float(info.get("format", {}).get("duration", 0) or 0),
            "codec": streams[0].get("codec_name", "?")}


def compress_audio(src: Path, dest: Path, on_progress=None,
                   duration: float = 0.0) -> None:
    """Transcode to a small mp3 — universally playable, and much smaller."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-vn", "-c:a", "libmp3lame", "-b:a", AUDIO_BITRATE]
    if on_progress and duration > 0:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(dest))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise ValueError("ffmpeg is not installed") from None
    if on_progress and duration > 0:
        for line in proc.stdout:
            m = _TIME_RE.search(line)
            if m:
                on_progress(min(0.999, int(m.group(1)) / 1_000_000 / duration))
    stderr = proc.communicate()[1]
    if proc.returncode != 0:
        tail = (stderr or "").strip().splitlines()
        raise ValueError(
            f"ffmpeg failed: {tail[-1] if tail else 'unknown error'}")
    if on_progress:
        on_progress(1.0)


def validate_audio(path: Path) -> None:
    probe_audio(path)
