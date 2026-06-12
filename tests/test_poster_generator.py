"""Unit tests for the poster generator service (PyMuPDF compositing).

These tests exercise _build_poster directly — no DB, no MinIO.
They verify that both rendering modes (gradient fallback and photo background)
produce a non-empty valid PNG and that the output size is plausible for a
2× supersampled A4 canvas.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from app.modules.recruitment.application.poster_generator_service import _build_poster


# ── Minimal VacancyExpanded stand-in ─────────────────────────────────────────

@dataclass
class _FakeVacancy:
    id: int = 1
    vacancy_name: str = "Backend Engineer"
    client_company: str = "Test Corp"
    contact: str = "Ana García"
    department: str = "IT"
    process: str = "Standard"
    career: str = "Ingeniería"
    city: str = "Quito"
    work_mode: str = "Híbrido"
    resource_level: str = "Senior"
    vacancy_status: str = "active"
    openings: int = 2
    experience_years: int = 3
    work_schedule: str | None = None
    project_duration_years: int = 1
    project_duration_months: int = 0
    description: str | None = None
    profile_requirements: dict[str, Any] | None = None
    profile_template_id: int | None = None
    is_active: bool = True
    created_at: datetime = datetime(2025, 1, 1)


def _is_valid_png(data: bytes) -> bool:
    """Return True if the bytes start with the PNG signature and have an IHDR chunk."""
    return data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Extract width and height from a PNG IHDR chunk."""
    # IHDR starts at byte 16; width=4 bytes, height=4 bytes
    width = struct.unpack(">I", data[16:20])[0]
    height = struct.unpack(">I", data[20:24])[0]
    return width, height


def _minimal_png(width: int = 4, height: int = 4) -> bytes:
    """Create a minimal valid PNG (solid red, tiny canvas) for testing."""
    import io
    try:
        import fitz  # Use PyMuPDF to create a tiny PNG so there's no PIL dependency
        doc = fitz.open()
        page = doc.new_page(width=width, height=height)
        page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=(1, 0, 0))
        pix = page.get_pixmap(alpha=False)
        return pix.tobytes("png")
    except Exception:
        # Fallback: manually construct a 1×1 white PNG
        def _chunk(tag: bytes, data: bytes) -> bytes:
            c = zlib.crc32(tag + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw = b"\x00\xff\xff\xff"  # filter byte + RGB white
        compressed = zlib.compress(raw)
        idat = _chunk(b"IDAT", compressed)
        iend = _chunk(b"IEND", b"")
        return signature + ihdr + idat + iend


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_poster_gradient_mode_produces_valid_png() -> None:
    """Without a base image the poster uses the brand gradient and is a valid PNG."""
    result = _build_poster(_FakeVacancy(), base_image_bytes=None)

    assert isinstance(result, bytes)
    assert len(result) > 50_000, "PNG should be substantial (2× supersampled A4)"
    assert _is_valid_png(result), "Output must start with PNG signature"


def test_poster_gradient_mode_dimensions() -> None:
    """2× supersampling doubles the canvas: expect 1588 × 2246 pixels."""
    result = _build_poster(_FakeVacancy(), base_image_bytes=None)
    w, h = _png_dimensions(result)
    assert w == 1588
    assert h == 2246


def test_poster_photo_mode_produces_valid_png() -> None:
    """With a base image the poster composites over the photo and is a valid PNG."""
    base_png = _minimal_png(width=100, height=150)
    result = _build_poster(_FakeVacancy(), base_image_bytes=base_png)

    assert isinstance(result, bytes)
    assert len(result) > 50_000
    assert _is_valid_png(result)


def test_poster_photo_mode_same_dimensions_as_gradient() -> None:
    """Photo mode output must have the same canvas size as gradient mode."""
    base_png = _minimal_png(width=200, height=300)
    result_photo = _build_poster(_FakeVacancy(), base_image_bytes=base_png)
    result_grad = _build_poster(_FakeVacancy(), base_image_bytes=None)

    assert _png_dimensions(result_photo) == _png_dimensions(result_grad)


def test_poster_photo_mode_differs_from_gradient() -> None:
    """The two rendering modes must produce different PNG bytes."""
    base_png = _minimal_png(width=200, height=300)
    result_photo = _build_poster(_FakeVacancy(), base_image_bytes=base_png)
    result_grad = _build_poster(_FakeVacancy(), base_image_bytes=None)

    assert result_photo != result_grad, "Photo and gradient posters should differ"


def test_poster_with_empty_requirements() -> None:
    """Vacancy with no profile_requirements should not raise."""
    vac = _FakeVacancy(profile_requirements=None)
    result = _build_poster(vac, base_image_bytes=None)
    assert _is_valid_png(result)


def test_poster_with_full_requirements() -> None:
    """Vacancy with all requirement buckets populated should render cleanly."""
    vac = _FakeVacancy(
        profile_requirements={
            "knowledge": ["Python", "SQL", "FastAPI"],
            "tools": ["Docker", "Git", "Redis"],
            "skills": ["Comunicación", "Liderazgo"],
            "certifications": ["AWS Cloud Practitioner"],
        }
    )
    result = _build_poster(vac, base_image_bytes=None)
    assert _is_valid_png(result)
