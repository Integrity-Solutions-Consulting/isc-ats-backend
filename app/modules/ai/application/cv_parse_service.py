"""CV structured data extraction using Gemini.

Reads the candidate's PDF, extracts work experience, projects, skills,
tools, soft skills, and certifications, then stores the result in
`recruitment.candidates.parsed_data` + updates `last_parsed_at`.

Education (title, university) is intentionally excluded — that data
will be captured through the candidate registration form.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import fitz  # PyMuPDF
from pypdf import PdfReader
from google import genai
from google.genai import types

import app.models_registry  # noqa: F401
from app.core.config import settings
from app.core.database import async_session_factory
from app.modules.recruitment.infrastructure.candidate_models import Candidate
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
  "experience": [
    {
      "company": "nombre de la empresa",
      "role": "cargo",
      "start_date": "mes y año, ej: Enero 2020",
      "end_date": "mes y año o Actualidad",
      "functions": ["función principal 1", "función principal 2"],
      "tools": ["herramienta o lenguaje 1"]
    }
  ],
  "projects": [
    {
      "name": "nombre del proyecto",
      "description": "descripción breve en 1-2 oraciones",
      "tools": ["herramienta 1"]
    }
  ],
  "skills": ["lenguaje o tecnología 1", "lenguaje o tecnología 2"],
  "tools": ["herramienta o programa 1"],
  "soft_skills": ["habilidad blanda 1"],
  "certifications": [
    {
      "institution": "institución",
      "name": "nombre del curso o certificación",
      "start": "Mes-Año",
      "end": "Mes-Año"
    }
  ]
}"""

_PROMPT_HEADER = (
    "Eres un extractor experto de información de CVs. "
    "Lee el CV y extrae los datos en el JSON indicado.\n\n"
    "REGLAS:\n"
    "- NO extraigas información educativa (carrera, universidad, título).\n"
    "- Incluye TODA la experiencia laboral que aparezca en el CV.\n"
    "- En 'skills' pon lenguajes de programación, frameworks y tecnologías.\n"
    "- En 'tools' pon herramientas, IDEs, plataformas y programas.\n"
    "- Los textos deben estar en español cuando sea posible.\n"
    "- Devuelve ÚNICAMENTE el JSON, sin texto adicional.\n\n"
    "Estructura exacta a devolver:\n"
)


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
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
            logger.warning("Model %s failed (%s) — %s", model, status_code, exc)
            last_exc = exc
            if not retryable:
                break
    raise RuntimeError(f"All models failed. Last error: {last_exc}") from last_exc


def _download_cv(bucket: str, stored_key: str) -> bytes:
    """Synchronous MinIO download — runs in a thread to avoid blocking the event loop."""
    obj = minio_client.get_object(bucket, stored_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


async def parse_candidate_cv(candidate_id: int) -> dict[str, Any] | None:
    """Parse a candidate's CV and store the result in candidate.parsed_data.

    Splits into three phases to keep the DB session closed during blocking I/O
    and the Gemini call, avoiding AsyncPG connection timeouts.
    """
    import asyncio

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — skipping CV parse for candidate %d", candidate_id)
        return None

    # ── Phase 1: read from DB ────────────────────────────────────────────────
    async with async_session_factory() as session:
        try:
            candidate: Candidate | None = await session.get(Candidate, candidate_id)
            if not candidate or not candidate.cv_file_id:
                logger.info("Candidate %d has no CV — skipping parse", candidate_id)
                return None
            cv_file: File | None = await session.get(File, candidate.cv_file_id)
            if not cv_file:
                return None
            bucket, stored_key = cv_file.bucket, cv_file.stored_key
        except Exception:
            logger.exception("CV parse failed reading DB for candidate %d", candidate_id)
            return None

    # ── Phase 2: blocking I/O + AI call — session closed ────────────────────
    try:
        pdf_bytes: bytes = await asyncio.to_thread(_download_cv, bucket, stored_key)

        cv_text = await asyncio.to_thread(_extract_text, pdf_bytes)
        if cv_text:
            contents = _build_text_contents(cv_text)
        else:
            images = await asyncio.to_thread(_pdf_to_images, pdf_bytes)
            if not images:
                logger.warning("Could not render CV for candidate %d", candidate_id)
                return None
            contents = _build_image_contents(images)

        result: dict[str, Any] = await asyncio.to_thread(_call_gemini, contents)
    except Exception:
        logger.exception("CV parse failed during processing for candidate %d", candidate_id)
        return None

    # ── Phase 3: write result — fresh session ────────────────────────────────
    async with async_session_factory() as session:
        try:
            candidate = await session.get(Candidate, candidate_id)
            if candidate:
                candidate.parsed_data = result
                candidate.last_parsed_at = datetime.now(timezone.utc)
                await session.commit()
                logger.info("CV parse complete for candidate %d", candidate_id)
            return result
        except Exception:
            await session.rollback()
            logger.exception("CV parse failed writing result for candidate %d", candidate_id)
            return None
