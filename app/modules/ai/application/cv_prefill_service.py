"""CV pre-fill extraction using Gemini.

Reads a candidate's uploaded PDF and extracts personal + education fields,
then fuzzy-matches catalog values (city, province, education_level, career,
university) against the org.parameters table and returns a structured dict
ready for the onboarding pre-fill response.

Experience and skills are intentionally excluded — those are handled by
cv_parse_service.py.
"""

from __future__ import annotations

import io
import json
import logging
import unicodedata
from difflib import SequenceMatcher
from typing import Any

import fitz  # PyMuPDF
from google import genai

try:
    import pdfplumber as _pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False
    from pypdf import PdfReader as _PdfReader
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

import app.models_registry  # noqa: F401
from app.core.config import settings
from app.modules.org.infrastructure.models import Parameter
from app.modules.org.infrastructure.parameters_repository import ParameterRepository
from app.modules.storage.infrastructure.models import File
from app.modules.storage.infrastructure.minio_client import minio_client

logger = logging.getLogger(__name__)

_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

_RETRYABLE_CODES = {429, 503, 500, 502, 504}

_JSON_SCHEMA = """\
{
  "first_name": "primer nombre",
  "last_name": "apellidos",
  "phone": "número de teléfono o celular (solo dígitos y signo +, sin espacios ni guiones)",
  "city": "ciudad de residencia",
  "province": "provincia o departamento de residencia",
  "education_level": "nivel de educación más alto (ej: Tercer nivel, Bachillerato, Maestría)",
  "career": "título o carrera universitaria más reciente",
  "university": "nombre de la universidad o institución educativa"
}"""

_PROMPT_HEADER = (
    "Eres un extractor experto de información de CVs. "
    "Lee el CV y extrae ÚNICAMENTE los datos personales y educativos indicados.\n\n"
    "REGLAS:\n"
    "- Extrae SOLO lo que esté visible en el CV.\n"
    "- Si un campo no se encuentra, devuelve null.\n"
    "- Teléfono: extrae solo los dígitos y el signo +; sin espacios ni guiones.\n"
    "- Devuelve ÚNICAMENTE el JSON, sin texto adicional.\n\n"
    "Estructura exacta a devolver:\n"
)


# ── Text utilities ────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip, and remove diacritics for fuzzy comparison."""
    text = text.lower().strip()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return text


def _match_catalog(extracted: str | None, catalog: list[Parameter]) -> int | None:
    """Fuzzy-match an extracted string against a catalog list, returning the id."""
    if not extracted:
        return None
    norm = _normalize(extracted)
    # 1. Exact match
    for p in catalog:
        if _normalize(p.name) == norm:
            return p.id
    # 2. Contains (either direction)
    for p in catalog:
        cn = _normalize(p.name)
        if norm in cn or cn in norm:
            return p.id
    # 3. Similarity >= 0.70 (handles abbreviations like "Univ. de Guayaquil")
    best_id, best_score = None, 0.0
    for p in catalog:
        score = SequenceMatcher(None, norm, _normalize(p.name)).ratio()
        if score > best_score:
            best_score = score
            best_id = p.id
    return best_id if best_score >= 0.70 else None


# ── PDF helpers (same pattern as cv_parse_service) ───────────────────────────

def _extract_text(pdf_bytes: bytes) -> str:
    if _HAS_PDFPLUMBER:
        with _pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    reader = _PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _pdf_to_images(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    scale = dpi / 72
    mat = fitz.Matrix(scale, scale)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def _build_text_contents(cv_text: str) -> list[Any]:
    return [_PROMPT_HEADER + _JSON_SCHEMA + f"\n\n## CV del candidato\n{cv_text[:14000]}"]


def _build_image_contents(images: list[bytes]) -> list[Any]:
    parts: list[Any] = [_PROMPT_HEADER + _JSON_SCHEMA + "\n\n## CV del candidato (páginas como imágenes)\n"]
    for img_bytes in images:
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
    return parts


def _call_gemini(contents: list[Any]) -> dict:
    client = genai.Client(api_key=settings.gemini_api_key)
    last_exc: Exception | None = None
    for model in _FALLBACK_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(response_mime_type="application/json"),
            )
            return json.loads(response.text)
        except Exception as exc:
            status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            retryable = status_code in _RETRYABLE_CODES or any(str(c) in str(exc) for c in _RETRYABLE_CODES)
            logger.warning("Prefill model %s failed (%s) — %s", model, status_code, exc)
            last_exc = exc
            if not retryable:
                break
    raise RuntimeError(f"All prefill models failed. Last error: {last_exc}") from last_exc


def _download_file(bucket: str, stored_key: str) -> bytes:
    """Synchronous MinIO download — runs in a thread to avoid blocking the event loop."""
    obj = minio_client.get_object(bucket, stored_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


# ── Public API ────────────────────────────────────────────────────────────────

async def prefill_from_cv(file_id: int, session: AsyncSession) -> dict:
    """Extract personal + education fields from a CV and fuzzy-match catalog IDs.

    Returns an empty dict when the file is not found, Gemini is not configured,
    or any unrecoverable error occurs — callers should handle the empty case
    gracefully rather than failing the onboarding flow.
    """
    import asyncio

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — skipping CV prefill for file %d", file_id)
        return {}

    # ── Phase 1: read file metadata from DB ──────────────────────────────────
    cv_file: File | None = await session.get(File, file_id)
    if not cv_file:
        logger.info("File %d not found — skipping prefill", file_id)
        return {}
    bucket, stored_key = cv_file.bucket, cv_file.stored_key

    # ── Phase 2: blocking I/O + AI call (session remains open but idle) ──────
    try:
        pdf_bytes: bytes = await asyncio.to_thread(_download_file, bucket, stored_key)

        cv_text = await asyncio.to_thread(_extract_text, pdf_bytes)
        if cv_text:
            contents = _build_text_contents(cv_text)
        else:
            images = await asyncio.to_thread(_pdf_to_images, pdf_bytes)
            if not images:
                logger.warning("Could not render CV for file %d", file_id)
                return {}
            contents = _build_image_contents(images)

        extracted: dict = await asyncio.to_thread(_call_gemini, contents)
    except Exception:
        logger.exception("CV prefill failed during processing for file %d", file_id)
        return {}

    # ── Phase 3: load catalogs and fuzzy-match ────────────────────────────────
    try:
        repo = ParameterRepository(session)
        cities, provinces, education_levels, careers, universities = await asyncio.gather(
            repo.get_all_by_type("city"),
            repo.get_all_by_type("province"),
            repo.get_all_by_type("education_level"),
            repo.get_all_by_type("career"),
            repo.get_all_by_type("university"),
        )
    except Exception:
        logger.exception("CV prefill failed loading catalogs for file %d", file_id)
        return {}

    return {
        "firstName": extracted.get("first_name"),
        "lastName": extracted.get("last_name"),
        "phone": extracted.get("phone"),
        "cityId": _match_catalog(extracted.get("city"), cities),
        "provinceId": _match_catalog(extracted.get("province"), provinces),
        "educationLevelId": _match_catalog(extracted.get("education_level"), education_levels),
        "careerId": _match_catalog(extracted.get("career"), careers),
        "universityId": _match_catalog(extracted.get("university"), universities),
    }
