"""Inline terminal image previews using half-block characters.

Each character cell shows two pixels (upper via foreground, lower via
background), so a preview is twice as tall in pixels as it is in text rows.
"""

import io

from PIL import Image, ImageSequence
from rich.text import Text

PREVIEW_WIDTH = 60          # character cells
MAX_PREVIEW_WIDTH = 120
MAX_ROWS = 20               # terminal rows an inline preview may occupy
ANIM_WIDTH = 40             # animations are smaller: they sit in a pane
ANIM_ROWS = 10
GIF_MAX_FRAMES = 60         # cap so a huge gif can't eat memory
SUPPORTED = {"png", "jpeg", "gif", "webp", "bmp"}


def validate_image(data: bytes) -> str:
    """Confirm the bytes are a real, decodable image. Returns the format.

    PIL's built-in decompression-bomb guard (Image.MAX_IMAGE_PIXELS) stays
    active, so oversized dimension attacks raise here instead of on display.
    """
    with Image.open(io.BytesIO(data)) as img:
        img.verify()
        fmt = (img.format or "").lower()
    if fmt not in SUPPORTED:
        raise ValueError(f"unsupported image format: {fmt or 'unknown'}")
    # verify() consumes the file; reopen and force a full decode
    with Image.open(io.BytesIO(data)) as img:
        img.load()
    return fmt


def is_animated(data: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return getattr(img, "n_frames", 1) > 1
    except Exception:
        return False


def _to_text(img: Image.Image, width: int, max_rows: int = MAX_ROWS) -> Text:
    img = img.convert("RGB")
    aspect = img.height / img.width
    w = max(4, min(width, MAX_PREVIEW_WIDTH))
    h = max(2, round(w * aspect))
    # A portrait picture would otherwise fill the whole window; shrink to fit
    # the row budget and keep the proportions.
    if h > max_rows * 2:
        scale = (max_rows * 2) / h
        w = max(4, int(w * scale))
        h = max_rows * 2
    h += h % 2                       # even rows so pixel pairs line up
    img = img.resize((w, h), Image.LANCZOS)
    px = img.load()

    text = Text()
    for y in range(0, h, 2):
        for x in range(w):
            tr, tg, tb = px[x, y]
            br, bg, bb = px[x, y + 1]
            text.append("▀", style=f"rgb({tr},{tg},{tb}) on rgb({br},{bg},{bb})")
        if y + 2 < h:
            text.append("\n")
    return text


def render_preview(data: bytes, width: int = PREVIEW_WIDTH,
                   max_rows: int = MAX_ROWS) -> Text:
    """Render the image (first frame, if animated) as colored half-blocks."""
    with Image.open(io.BytesIO(data)) as src:
        return _to_text(src, width, max_rows)


def render_frames(data: bytes, width: int = ANIM_WIDTH,
                  max_rows: int = ANIM_ROWS) -> tuple[list[Text], float]:
    """Render every frame of an animation. Returns (frames, seconds_per_frame)."""
    frames: list[Text] = []
    delay_ms = 100
    with Image.open(io.BytesIO(data)) as src:
        for i, frame in enumerate(ImageSequence.Iterator(src)):
            if i >= GIF_MAX_FRAMES:
                break
            if i == 0:
                delay_ms = frame.info.get("duration", 100) or 100
            frames.append(_to_text(frame, width, max_rows))
    # Terminals cannot keep up with 10ms frames, and some gifs claim 0.
    return frames, max(0.05, delay_ms / 1000)


def make_thumb(data: bytes, width: int = 480, max_bytes: int = 120 * 1024
               ) -> bytes | None:
    """A small JPEG of a picture, so it can be previewed without downloading
    the original."""
    try:
        with Image.open(io.BytesIO(data)) as src:
            img = src.convert("RGB")
            if img.width > width:
                h = max(1, round(img.height * width / img.width))
                img = img.resize((width, h), Image.LANCZOS)
            for quality in (70, 55, 40):
                out = io.BytesIO()
                img.save(out, "JPEG", quality=quality)
                if out.tell() <= max_bytes:
                    return out.getvalue()
    except Exception:
        return None
    return None
