"""CV pre-fill extraction using Gemini.

Reads a candidate's uploaded PDF and extracts personal + education fields,
then fuzzy-matches catalog values (city, education_level, career, title,
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
import pdfplumber
from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

import app.models_registry  # noqa: F401
from app.core.config import settings
from app.modules.org.infrastructure.models import Parameter
from app.modules.storage.infrastructure.minio_client import minio_client
from app.modules.storage.infrastructure.models import File

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
  "id_number": "cédula o pasaporte (solo letras y dígitos, sin espacios)",
  "birth_date": "fecha de nacimiento en formato estricto YYYY-MM-DD",
  "phone": "número de teléfono o celular (solo dígitos y signo +, sin espacios ni guiones)",
  "home_address": "dirección domiciliaria o de residencia del candidato",
  "current_company": "nombre de la empresa donde trabaja ACTUALMENTE",
  "city": "ciudad de residencia",
  "education_level": "nivel de educación más alto (ej: Tercer nivel, Bachillerato, Maestría)",
  "career": "SOLO el campo de estudio, sin el grado (ej: 'Software', no 'Ingeniería en Software')",
  "title": "SOLO el grado obtenido, sin el campo (ej: Ingeniero, Tecnólogo, Licenciado)",
  "university": "nombre de la universidad o institución educativa"
}"""

_PROMPT_HEADER = (
    "Eres un extractor experto de información de CVs. "
    "Lee el CV y extrae ÚNICAMENTE los datos personales y educativos indicados.\n\n"
    "REGLAS:\n"
    "- Extrae SOLO lo que esté visible en el CV.\n"
    "- Si un campo no se encuentra, devuelve null. NO inventes ni infieras datos.\n"
    "- id_number: solo el documento de identidad de la PERSONA; sin espacios ni guiones.\n"
    "- birth_date: úsala SOLO si está la fecha completa; si falta día o mes, devuelve null.\n"
    "- home_address: dirección de residencia del candidato, NO direcciones de empresas.\n"
    "- current_company: solo si el empleo está VIGENTE (ej: 'Actualidad', "
    "'Presente', sin fecha de fin); si no, devuelve null.\n"
    "- career vs title: separa SIEMPRE el campo del grado. "
    "'Ingeniero en Software' → career='Software', title='Ingeniero'.\n"
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
    """Fuzzy-match an extracted string against a catalog, returning the best id.

    Scores every entry and keeps the highest — never the first that happens to
    match. An exact (normalised) hit wins immediately. Containment is treated as
    a strong signal but still competes on specificity, so "Administración" maps
    to the closest-length option instead of whichever was inserted first.
    """
    if not extracted:
        return None
    norm = _normalize(extracted)
    best_id, best_score = None, 0.0
    for p in catalog:
        cn = _normalize(p.name)
        if cn == norm:
            return p.id
        score = SequenceMatcher(None, norm, cn).ratio()
        if cn and (norm in cn or cn in norm):
            shorter, longer = min(len(norm), len(cn)), max(len(norm), len(cn))
            score = max(score, 0.60 + 0.40 * (shorter / longer))
        if score > best_score:
            best_score, best_id = score, p.id
    return best_id if best_score >= 0.70 else None


# ── PDF helpers (same pattern as cv_parse_service) ───────────────────────────

def _extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()


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


_CATALOG_TYPES = ("city", "education_level", "career", "title", "university")


async def _load_catalogs(session: AsyncSession) -> dict[str, list[Parameter]]:
    """Load every prefill catalog in a SINGLE query, partitioned by type.

    Must NOT fan out into one query per type via asyncio.gather: an AsyncSession
    backs a single connection and forbids concurrent operations, so gathering
    several queries on the same session raises InvalidRequestError. One round-trip
    with `type IN (...)` is both correct and cheaper.
    """
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(Parameter)
            .where(Parameter.type.in_(_CATALOG_TYPES))
            .where(Parameter.is_active.is_(True))
        )
    ).scalars().all()

    by_type: dict[str, list[Parameter]] = {t: [] for t in _CATALOG_TYPES}
    for p in rows:
        by_type[p.type].append(p)
    return by_type


# ── Public API ────────────────────────────────────────────────────────────────

async def prefill_from_bytes(pdf_bytes: bytes, session: AsyncSession) -> dict:
    """Extract personal + education fields from raw CV bytes and match catalog IDs.

    The PDF is processed entirely in memory — nothing is persisted. This is the
    path used by onboarding so a candidate's CV is never stored until they
    finish registration (LOPDP data-minimisation). Returns an empty dict when
    Gemini is not configured or any unrecoverable error occurs — callers should
    handle the empty case gracefully rather than failing the onboarding flow.
    """
    import asyncio

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — skipping CV prefill")
        return {}

    # ── AI extraction (blocking I/O + AI call run in threads) ────────────────
    try:
        cv_text = await asyncio.to_thread(_extract_text, pdf_bytes)
        if cv_text:
            contents = _build_text_contents(cv_text)
        else:
            images = await asyncio.to_thread(_pdf_to_images, pdf_bytes)
            if not images:
                logger.warning("Could not render CV for prefill")
                return {}
            contents = _build_image_contents(images)

        extracted: dict = await asyncio.to_thread(_call_gemini, contents)
    except Exception:
        logger.exception("CV prefill failed during processing")
        return {}

    # ── Load catalogs and fuzzy-match ────────────────────────────────────────
    try:
        catalogs = await _load_catalogs(session)
    except Exception:
        logger.exception("CV prefill failed loading catalogs")
        return {}

    return {
        "firstName": extracted.get("first_name"),
        "lastName": extracted.get("last_name"),
        "idNumber": extracted.get("id_number"),
        "birthDate": extracted.get("birth_date"),
        "phone": extracted.get("phone"),
        "homeAddress": extracted.get("home_address"),
        "currentCompany": extracted.get("current_company"),
        "cityId": _match_catalog(extracted.get("city"), catalogs["city"]),
        "educationLevelId": _match_catalog(
            extracted.get("education_level"), catalogs["education_level"]
        ),
        "careerId": _match_catalog(extracted.get("career"), catalogs["career"]),
        "titleId": _match_catalog(extracted.get("title"), catalogs["title"]),
        "universityId": _match_catalog(extracted.get("university"), catalogs["university"]),
    }


async def prefill_from_cv(file_id: int, session: AsyncSession) -> dict:
    """Download a CV already stored in MinIO by file_id and run prefill on it.

    Kept for re-processing an already-persisted CV (e.g. from "Mi perfil"). The
    onboarding flow does NOT use this — it calls prefill_from_bytes directly so
    nothing is stored until the candidate finishes registration.
    """
    import asyncio

    cv_file: File | None = await session.get(File, file_id)
    if not cv_file:
        logger.info("File %d not found — skipping prefill", file_id)
        return {}

    try:
        pdf_bytes: bytes = await asyncio.to_thread(
            _download_file, cv_file.bucket, cv_file.stored_key
        )
    except Exception:
        logger.exception("CV prefill failed downloading file %d", file_id)
        return {}

    return await prefill_from_bytes(pdf_bytes, session)
