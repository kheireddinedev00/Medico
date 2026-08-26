"""Minimal, non-destructive image preprocessing.

Vision-language models generally read a clean high-resolution original better than
one that has been aggressively filtered, so we do only two safe things:
  1. Apply EXIF orientation so sideways phone photos are upright.
  2. Upscale images whose long edge is below a threshold, so small/low-DPI captures
     have enough pixels for fine print (digits, decimal points).
We never denoise, binarize, or sharpen here — that risks erasing faint characters.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps

from config import MIN_IMAGE_LONG_EDGE


def _upscale_if_small(image: Image.Image, min_long_edge: int) -> Image.Image:
    long_edge = max(image.size)
    if long_edge >= min_long_edge:
        return image
    scale = min_long_edge / long_edge
    new_size = (round(image.width * scale), round(image.height * scale))
    return image.resize(new_size, Image.LANCZOS)


def preprocess_image(image: Image.Image, min_long_edge: int | None = None) -> Image.Image:
    # exif_transpose respects the camera orientation tag, then strips it.
    image = ImageOps.exif_transpose(image)
    image = _upscale_if_small(image, min_long_edge or MIN_IMAGE_LONG_EDGE)
    return image


def preprocess_images(images: list[Image.Image], min_long_edge: int | None = None) -> list[Image.Image]:
    return [preprocess_image(img, min_long_edge) for img in images]


def image_to_data_url(image: Image.Image) -> str:
    """Encode a PIL image as a base64 PNG data URL for the chat image payload."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
