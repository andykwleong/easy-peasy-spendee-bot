from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO


@dataclass(frozen=True)
class PreparedImage:
    content: bytes
    mime_type: str
    original_bytes: int
    prepared_bytes: int
    original_size: tuple[int, int] | None
    prepared_size: tuple[int, int] | None
    resized: bool
    note: str | None = None


def prepare_image_for_vision(
    image_bytes: bytes,
    mime_type: str,
    *,
    max_side: int = 1280,
    jpeg_quality: int = 82,
) -> PreparedImage:
    original = PreparedImage(
        content=image_bytes,
        mime_type=mime_type,
        original_bytes=len(image_bytes),
        prepared_bytes=len(image_bytes),
        original_size=None,
        prepared_size=None,
        resized=False,
    )

    try:
        from PIL import Image, ImageOps
    except ModuleNotFoundError:
        return PreparedImage(
            **{**original.__dict__, "note": "Pillow is not installed; sent original image."}
        )

    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            image = ImageOps.exif_transpose(opened)
            original_size = image.size
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            output = BytesIO()
            image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
            prepared = output.getvalue()

        if len(prepared) >= len(image_bytes):
            return PreparedImage(
                content=image_bytes,
                mime_type=mime_type,
                original_bytes=len(image_bytes),
                prepared_bytes=len(image_bytes),
                original_size=original_size,
                prepared_size=original_size,
                resized=False,
                note="Original image was already smaller than the prepared JPEG.",
            )

        return PreparedImage(
            content=prepared,
            mime_type="image/jpeg",
            original_bytes=len(image_bytes),
            prepared_bytes=len(prepared),
            original_size=original_size,
            prepared_size=image.size,
            resized=image.size != original_size,
        )
    except Exception as exc:
        return PreparedImage(
            **{
                **original.__dict__,
                "note": f"Image preparation failed; sent original image. {type(exc).__name__}: {exc}",
            }
        )
