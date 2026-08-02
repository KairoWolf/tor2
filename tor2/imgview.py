"""Inline terminal image previews using half-block characters."""

import io

from PIL import Image
from rich.text import Text

PREVIEW_WIDTH = 48  # character cells


def validate_image(data: bytes) -> str:
    """Confirm the bytes are a real, decodable image. Returns the format.

    PIL's built-in decompression-bomb guard (Image.MAX_IMAGE_PIXELS) stays
    active, so oversized dimension attacks raise here instead of on display.
    """
    with Image.open(io.BytesIO(data)) as img:
        img.verify()
        fmt = (img.format or "").lower()
    if fmt not in {"png", "jpeg", "gif", "webp", "bmp"}:
        raise ValueError(f"unsupported image format: {fmt or 'unknown'}")
    # verify() consumes the file; reopen and force a full decode
    with Image.open(io.BytesIO(data)) as img:
        img.load()
    return fmt


def render_preview(data: bytes, width: int = PREVIEW_WIDTH) -> Text:
    """Render an image as colored ▀ half-blocks (2 pixels per cell row)."""
    with Image.open(io.BytesIO(data)) as src:
        img = src.convert("RGB")
        aspect = img.height / img.width
        w = min(width, img.width, 120)
        h = max(2, round(w * aspect))
        h += h % 2  # even row count so pairs line up
        img = img.resize((w, h))
        px = img.load()

        text = Text()
        for y in range(0, h, 2):
            for x in range(w):
                tr, tg, tb = px[x, y]
                br, bg_, bb = px[x, y + 1]
                text.append(
                    "▀",
                    style=f"rgb({tr},{tg},{tb}) on rgb({br},{bg_},{bb})",
                )
            text.append("\n")
        return text
