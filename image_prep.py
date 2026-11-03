"""Make phone photos small enough for Vinted's upload limit (originals are never modified)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhone/Samsung HEIC photos
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

MAX_BYTES = 7 * 1024 * 1024
from paths import UPLOAD_CACHE as CACHE


def upload_ready(image: str | Path, max_bytes: int = MAX_BYTES) -> str:
    """Return a path to upload: the original if it's a small JPEG/PNG, else a shrunk JPEG copy (<= max_bytes)."""
    src = Path(image)
    if src.stat().st_size <= max_bytes and src.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return str(src)

    CACHE.mkdir(exist_ok=True)
    out = CACHE / f"{src.stem}_{int(src.stat().st_mtime)}.jpg"
    if out.exists() and out.stat().st_size <= max_bytes:
        return str(out)

    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # keep the phone's rotation
        im = im.convert("RGB")
        quality, scale = 90, 1.0
        while True:
            frame = im if scale == 1.0 else im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            frame.save(buf, "JPEG", quality=quality, optimize=True)
            if buf.tell() <= max_bytes:
                break
            if quality > 75:
                quality -= 5  # first try a bit more compression
            else:
                scale *= 0.85  # then reduce the resolution
    out.write_bytes(buf.getvalue())
    return str(out)
