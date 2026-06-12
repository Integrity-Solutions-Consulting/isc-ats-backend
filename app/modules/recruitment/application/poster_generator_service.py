"""Generates a hiring poster PNG for a vacancy using PyMuPDF (server-side compositing).

Architecture:
- Reads vacancy data AND the latest vacancy_image file record from the DB (phase 1, async).
- If a base image exists, fetches its bytes from MinIO (phase 1b, sync in thread).
- Composites the poster in a thread using PyMuPDF/fitz (phase 2, sync I/O).
- Returns raw PNG bytes — no DB writes, no MinIO storage.

Two rendering modes
-------------------
WITH base image
  The uploaded photo fills the A4 canvas as a full-bleed background.
  A semi-transparent dark scrim covers the bottom ~55 % of the canvas so that
  all text elements remain readable over any photo.
  Text layout is identical to the gradient-only mode — brand colours on dark.

WITHOUT base image (fallback)
  Top-to-bottom brand gradient (dark navy → mid-blue), same layout.

The Integrity Solutions brand colours are applied directly.  No external AI
image API is called; the design is produced deterministically from vacancy data.

Imagen 4 integration note:
  The product owner's API key only has Imagen 4 models (Generate / Ultra / Fast).
  Imagen 4 is a text-to-image model that does NOT support image-to-image compositing,
  so it cannot reliably overlay structured text on a base image.  Server-side
  compositing with PyMuPDF gives pixel-perfect, quota-free, bilingual results.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import fitz  # PyMuPDF
from sqlalchemy import select

import app.models_registry  # noqa: F401
from app.core.database import async_session_factory
from app.modules.recruitment.infrastructure.vacancies_repository import VacanciesExpandedRepository
from app.modules.storage.infrastructure.models import File
from app.modules.storage.infrastructure.minio_client import minio_client

logger = logging.getLogger(__name__)

# ── Integrity Solutions brand palette ─────────────────────────────────────────
# Colours as (R, G, B) floats in [0, 1]
_DARK_NAVY   = (0.055, 0.118, 0.247)   # #0E1E3F
_MID_BLUE    = (0.094, 0.255, 0.490)   # #18417D
_ACCENT_BLUE = (0.137, 0.439, 0.749)   # #2370BF
_LIGHT_BLUE  = (0.620, 0.800, 0.937)   # #9ECBEF
_WHITE       = (1.0,   1.0,   1.0)
_DARK_TEXT   = (0.133, 0.133, 0.133)   # near-black for readability on light bg

# Poster canvas
_W, _H = 794, 1123          # A4 portrait at 96 dpi
_MARGIN = 48

# Scrim: covers the lower portion of the image so text is readable
_SCRIM_START_FRACTION = 0.25   # scrim gradient starts at 25 % from top
_SCRIM_OPACITY = 0.82          # maximum opacity of the dark overlay


def _fmt_list(items: list[str] | None, max_items: int = 5) -> list[str]:
    if not items:
        return []
    return [str(i) for i in items[:max_items]]


# ── Background helpers ────────────────────────────────────────────────────────

def _draw_gradient_background(page: fitz.Page) -> None:
    """Draw a top-to-bottom gradient from dark navy to mid-blue."""
    steps = 60
    step_h = _H / steps
    for i in range(steps):
        t = i / (steps - 1)
        r = _DARK_NAVY[0] + t * (_MID_BLUE[0] - _DARK_NAVY[0])
        g = _DARK_NAVY[1] + t * (_MID_BLUE[1] - _DARK_NAVY[1])
        b = _DARK_NAVY[2] + t * (_MID_BLUE[2] - _DARK_NAVY[2])
        rect = fitz.Rect(0, i * step_h, _W, (i + 1) * step_h + 1)
        page.draw_rect(rect, color=None, fill=(r, g, b))


def _draw_photo_background(page: fitz.Page, image_bytes: bytes) -> None:
    """Place the photo as a full-bleed background, then add a dark scrim.

    The image is cover-fitted to the canvas: it is scaled so its shorter
    dimension fills the canvas, then centred.  A vertical gradient scrim
    (transparent at top → _DARK_NAVY at bottom) is drawn over it so the
    text area at the bottom remains legible.
    """
    # ── Insert base image ────────────────────────────────────────────────────
    img_rect = fitz.Rect(0, 0, _W, _H)
    page.insert_image(img_rect, stream=image_bytes, keep_proportion=False)

    # ── Dark scrim — gradient from transparent to opaque navy ────────────────
    scrim_steps = 60
    scrim_start_y = _H * _SCRIM_START_FRACTION
    scrim_height = _H - scrim_start_y
    step_h = scrim_height / scrim_steps
    for i in range(scrim_steps):
        t = i / (scrim_steps - 1)
        opacity = t * _SCRIM_OPACITY
        rect = fitz.Rect(0, scrim_start_y + i * step_h,
                         _W, scrim_start_y + (i + 1) * step_h + 1)
        page.draw_rect(rect, color=None, fill=_DARK_NAVY, fill_opacity=opacity)


# ── Layout helpers ────────────────────────────────────────────────────────────

def _draw_accent_bar(page: fitz.Page, y: float, text: str) -> float:
    """Draw a translucent accent banner with white text. Returns bottom y."""
    bar_h = 38
    page.draw_rect(
        fitz.Rect(0, y, _W, y + bar_h),
        color=None,
        fill=_ACCENT_BLUE,
        fill_opacity=0.85,
    )
    page.insert_text(
        fitz.Point(_MARGIN, y + bar_h - 10),
        text,
        fontname="Helvetica-Bold",
        fontsize=14,
        color=_WHITE,
    )
    return y + bar_h + 8


def _insert_wrapped_text(
    page: fitz.Page,
    text: str,
    rect: fitz.Rect,
    fontname: str = "Helvetica",
    fontsize: float = 11,
    color: tuple = _WHITE,
) -> float:
    """Insert text wrapping within rect. Returns the Y position after the last line."""
    rc = page.insert_textbox(
        rect,
        text,
        fontname=fontname,
        fontsize=fontsize,
        color=color,
        align=fitz.TEXT_ALIGN_LEFT,
    )
    # rc is the remaining (unused) height; negative means overflow
    used = rect.height - max(rc, 0)
    return rect.y0 + used


def _draw_section(
    page: fitz.Page,
    x: float,
    y: float,
    w: float,
    title: str,
    items: list[str],
) -> float:
    """Draw a titled bullet section. Returns the bottom y."""
    # Section title
    page.insert_text(
        fitz.Point(x, y + 14),
        title.upper(),
        fontname="Helvetica-Bold",
        fontsize=9,
        color=_LIGHT_BLUE,
    )
    y += 22
    if not items:
        page.insert_text(
            fitz.Point(x + 6, y + 11),
            "—",
            fontname="Helvetica",
            fontsize=10,
            color=_WHITE,
        )
        return y + 20

    for item in items:
        bullet_text = f"• {item}"
        rect = fitz.Rect(x, y, x + w, y + 40)
        end_y = _insert_wrapped_text(page, bullet_text, rect, fontsize=10)
        y = end_y + 4
    return y + 6


# ── Poster builder ────────────────────────────────────────────────────────────

def _build_poster(vacancy: Any, base_image_bytes: bytes | None) -> bytes:
    """Composite the poster.  vacancy is a VacancyExpanded dataclass instance."""
    doc = fitz.open()
    page = doc.new_page(width=_W, height=_H)

    if base_image_bytes:
        _draw_photo_background(page, base_image_bytes)
    else:
        _draw_gradient_background(page)

    # When a photo is used the content starts lower so the image top is visible.
    y = _MARGIN if not base_image_bytes else int(_H * _SCRIM_START_FRACTION) + 16

    # ── Top badge ─────────────────────────────────────────────────────────────
    badge_rect = fitz.Rect(_MARGIN, y, _W - _MARGIN, y + 28)
    page.draw_rect(badge_rect, color=_ACCENT_BLUE, fill=_ACCENT_BLUE, fill_opacity=0.4)
    page.insert_text(
        fitz.Point(_MARGIN + 10, y + 19),
        "WE'RE HIRING  ·  ÚNETE AL TEAM",
        fontname="Helvetica-Bold",
        fontsize=11,
        color=_LIGHT_BLUE,
    )
    y += 38

    # ── Vacancy name ──────────────────────────────────────────────────────────
    name_rect = fitz.Rect(_MARGIN, y, _W - _MARGIN, y + 80)
    _insert_wrapped_text(
        page,
        vacancy.vacancy_name,
        name_rect,
        fontname="Helvetica-Bold",
        fontsize=30,
        color=_WHITE,
    )
    y += 90

    # ── Key data row ──────────────────────────────────────────────────────────
    page.draw_line(fitz.Point(_MARGIN, y), fitz.Point(_W - _MARGIN, y), color=_ACCENT_BLUE, width=1)
    y += 10

    exp = vacancy.experience_years or 0
    data_items = [
        f"Carrera: {vacancy.career}",
        f"Ciudad: {vacancy.city}",
        f"Experiencia: {exp} año{'s' if exp != 1 else ''}",
        f"Modalidad: {vacancy.work_mode}",
    ]
    col_w = (_W - 2 * _MARGIN) / 2
    for i, item in enumerate(data_items):
        xi = _MARGIN + (i % 2) * col_w
        yi = y + (i // 2) * 18 + 13
        page.insert_text(fitz.Point(xi, yi), item, fontname="Helvetica", fontsize=10, color=_WHITE)
    y += (len(data_items) // 2 + 1) * 18 + 8

    page.draw_line(fitz.Point(_MARGIN, y), fitz.Point(_W - _MARGIN, y), color=_ACCENT_BLUE, width=1)
    y += 16

    # ── Requirements grid (2-column) ──────────────────────────────────────────
    req = vacancy.profile_requirements or {}
    knowledge = _fmt_list(req.get("knowledge"))
    tools = _fmt_list(req.get("tools"))
    skills = _fmt_list(req.get("skills"))
    certifications = _fmt_list(req.get("certifications"))

    col_w2 = (_W - 2 * _MARGIN - 16) / 2
    left_x = _MARGIN
    right_x = _MARGIN + col_w2 + 16

    y_left = _draw_section(page, left_x, y, col_w2, "Conocimientos", knowledge)
    y_right = _draw_section(page, right_x, y, col_w2, "Herramientas", tools)
    y = max(y_left, y_right) + 8

    y_left = _draw_section(page, left_x, y, col_w2, "Habilidades", skills)
    y_right = _draw_section(page, right_x, y, col_w2, "Certificaciones", certifications)
    y = max(y_left, y_right) + 16

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_y = _H - 60
    page.draw_rect(
        fitz.Rect(0, footer_y, _W, _H),
        color=None,
        fill=_DARK_NAVY,
    )
    page.insert_text(
        fitz.Point(_MARGIN, footer_y + 22),
        "Postula en: integritysolutions.com.ec  ·  careers@integritysolutions.com.ec",
        fontname="Helvetica",
        fontsize=9,
        color=_LIGHT_BLUE,
    )
    page.insert_text(
        fitz.Point(_MARGIN, footer_y + 42),
        "Integrity Solutions  ·  Soluciones de Talento Humano",
        fontname="Helvetica-Bold",
        fontsize=10,
        color=_WHITE,
    )

    # ── Render to PNG ─────────────────────────────────────────────────────────
    mat = fitz.Matrix(2.0, 2.0)   # 2× supersampling → crisp at 192 dpi
    pix = page.get_pixmap(matrix=mat, alpha=False)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def _fetch_from_minio(bucket: str, stored_key: str) -> bytes:
    """Fetch binary data from MinIO synchronously (called inside a thread)."""
    obj = minio_client.get_object(bucket, stored_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


# ── Public entry point ────────────────────────────────────────────────────────

async def generate_vacancy_poster(vacancy_id: int) -> bytes:
    """Phase 1: read vacancy and base-image file record from DB (session closed after).
    Phase 1b: fetch base image bytes from MinIO if available (in thread, avoids blocking).
    Phase 2: composite the poster in a thread. Returns raw PNG bytes."""
    # ── Phase 1 ───────────────────────────────────────────────────────────
    async with async_session_factory() as session:
        vacancy = await VacanciesExpandedRepository(session).get_expanded(vacancy_id)

        base_image_file: File | None = None
        if vacancy is not None:
            # Pick the most-recently created active vacancy_image for this vacancy
            stmt = (
                select(File)
                .where(File.entity_type == "vacancy_image")
                .where(File.entity_id == vacancy_id)
                .where(File.is_active.is_(True))
                .order_by(File.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            base_image_file = result.scalar_one_or_none()

    if vacancy is None:
        raise ValueError(f"Vacancy {vacancy_id} not found or inactive")

    # ── Phase 1b ──────────────────────────────────────────────────────────
    base_image_bytes: bytes | None = None
    if base_image_file is not None:
        try:
            base_image_bytes = await asyncio.to_thread(
                _fetch_from_minio, base_image_file.bucket, base_image_file.stored_key
            )
            logger.info(
                "Loaded base image for vacancy %d: file_id=%d size=%d bytes",
                vacancy_id, base_image_file.id, len(base_image_bytes),
            )
        except Exception:
            logger.warning(
                "Could not fetch base image for vacancy %d (file_id=%d) — falling back to gradient",
                vacancy_id, base_image_file.id, exc_info=True,
            )
            base_image_bytes = None

    # ── Phase 2 ───────────────────────────────────────────────────────────
    mode = "photo" if base_image_bytes else "gradient"
    logger.info("Generating PyMuPDF poster for vacancy %d (mode=%s)", vacancy_id, mode)
    image_bytes: bytes = await asyncio.to_thread(_build_poster, vacancy, base_image_bytes)
    logger.info("Poster ready: vacancy=%d mode=%s size=%d bytes", vacancy_id, mode, len(image_bytes))
    return image_bytes
