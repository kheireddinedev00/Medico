"""Detect the uploaded file type and load it into a list of PIL images.

A multi-page PDF becomes one image per page; a single image file becomes a
one-element list. Everything downstream works on `list[PIL.Image]`, so the rest of
the pipeline never has to care whether the source was a PDF or a photo.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from config import PDF_RENDER_DPI

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
PDF_SUFFIXES = {".pdf"}


class UnsupportedFileError(ValueError):
    pass


def detect_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    raise UnsupportedFileError(
        f"Unsupported file type '{suffix}'. Supported: PDF and "
        f"{', '.join(sorted(IMAGE_SUFFIXES))}."
    )


def _pdf_to_images(path: Path, dpi: int) -> list[Image.Image]:
    images: list[Image.Image] = []
    with fitz.open(path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi)
            images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    return images


def load_images(path: Path, dpi: int | None = None) -> list[Image.Image]:
    """Return the report as a list of RGB PIL images (one per page)."""
    file_type = detect_file_type(path)
    if file_type == "pdf":
        return _pdf_to_images(path, dpi or PDF_RENDER_DPI)
    return [Image.open(path).convert("RGB")]
