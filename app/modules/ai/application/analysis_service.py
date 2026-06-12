"""CV-vs-vacancy analysis using Gemini.

Called as a FastAPI BackgroundTask after a new application is created.
Runs in its own DB session — never reuses the request session.

Strategy:
  1. Try to extract text from the PDF with pypdf (fast, no tokens for images).
  2. If the PDF has no extractable text (scanned), render pages as images with
     PyMuPDF and send them to Gemini as multimodal input.
  3. All Gemini calls go through a fallback chain — if one model returns a rate
     limit or transient error, the next one is tried automatically.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import fitz  # PyMuPDF
from pypdf import PdfReader
from google import genai
from google.genai import types

import app.models_registry  # noqa: F401 — ensures all FK targets are registered
from app.core.config import settings
from app.core.database import async_session_factory
from app.modules.recruitment.infrastructure.application_models import Application
from app.modules.recruitment.infrastructure.applications_repository import ApplicationRepository
from app.modules.recruitment.infrastructure.candidate_models import Candidate
from app.modules.recruitment.infrastructure.models import Vacancy
from app.modules.storage.infrastructure.models import File
from app.modules.storage.infrastructure.minio_client import minio_client

logger = logging.getLogger(__name__)

# Models tried in order — first success wins.
_FALLBACK_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-preview",
]

# Errors that warrant trying the next model.
_RETRYABLE_CODES = {429, 503, 500, 502, 504}

_REQUIREMENTS_BLOCK = """\
## Requisitos de la vacante
**Puesto**: {title}
**Experiencia requerida**: {experience_years} años
**Descripción**: {description}

**Conocimientos requeridos**: {knowledge}
**Herramientas requeridas**: {tools}
**Habilidades requeridas**: {skills}
**Certificaciones requeridas**: {certifications}
"""

_JSON_INSTRUCTIONS = """\
Devuelve ÚNICAMENTE un JSON con esta estructura exacta (en español):
{
  "score": <número entero 0-100>,
  "summary": "<2-3 oraciones describiendo el perfil del candidato respecto a la vacante>",
  "strengths": ["<fortaleza 1>", "<fortaleza 2>"],
  "gaps": ["<brecha 1>", "<brecha 2>"],
  "skills": [{"label": "<nombre>", "match": "<match|miss|neutral>"}, ...],
  "tools": [{"label": "<nombre>", "match": "<match|miss|neutral>"}, ...],
  "softSkills": [{"label": "<nombre>", "match": "<match|miss|neutral>"}, ...],
  "certifications": [{"label": "<nombre>", "match": "<match|miss|neutral>"}, ...]
}

Reglas para "match":
- "match": requisito de la vacante presente en el CV
- "miss": requisito de la vacante NO encontrado en el CV
- "neutral": habilidad del CV no requerida por la vacante

Incluye TODOS los requisitos de la vacante (match o miss) y TODOS los skills
encontrados en el CV que no estén ya listados (estos van como "neutral").
No incluyas nada fuera del JSON.
"""


def _requirements_block(vacancy: Vacancy) -> str:
    req = vacancy.profile_requirements or {}
    return _REQUIREMENTS_BLOCK.format(
        title=getattr(vacancy, "vacancy_name_id", ""),
        experience_years=getattr(vacancy, "experience_years", 0),
        description=getattr(vacancy, "description", "") or "",
        knowledge=", ".join(req.get("knowledge", [])) or "No especificado",
        tools=", ".join(req.get("tools", [])) or "No especificado",
        skills=", ".join(req.get("skills", [])) or "No especificado",
        certifications=", ".join(req.get("certifications", [])) or "No especificado",
    )


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _pdf_to_images(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    """Render each PDF page to a PNG image using PyMuPDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    scale = dpi / 72  # PDF points are 72 dpi
    mat = fitz.Matrix(scale, scale)
    images = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))
    doc.close()
    return images


def _build_text_contents(cv_text: str, vacancy: Vacancy) -> list[Any]:
    prompt = (
        "Eres un reclutador experto. Analiza qué tan bien el CV del candidato "
        "encaja con los requisitos de la vacante.\n\n"
        + _requirements_block(vacancy)
        + f"\n## CV del candidato\n{cv_text[:12000]}\n\n"
        + _JSON_INSTRUCTIONS
    )
    return [prompt]


def _build_image_contents(images: list[bytes], vacancy: Vacancy) -> list[Any]:
    """Multimodal contents: vacancy requirements + CV page images."""
    header = (
        "Eres un reclutador experto. A continuación se muestran las páginas del CV "
        "del candidato como imágenes. Analiza el CV visualmente y compáralo con los "
        "requisitos de la vacante.\n\n"
        + _requirements_block(vacancy)
        + "\n## CV del candidato (páginas como imágenes)\n"
    )
    parts: list[Any] = [header]
    for img_bytes in images:
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
    parts.append("\n" + _JSON_INSTRUCTIONS)
    return parts


def _call_gemini(contents: list[Any]) -> dict:
    """Try each model in the fallback chain; raise on total failure."""
    client = genai.Client(api_key=settings.gemini_api_key)
    last_exc: Exception | None = None

    for model in _FALLBACK_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)
        except Exception as exc:
            status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            retryable = (
                status_code in _RETRYABLE_CODES
                or any(str(c) in str(exc) for c in _RETRYABLE_CODES)
            )
            logger.warning("Model %s failed (%s) — %s", model, status_code, exc)
            last_exc = exc
            if not retryable:
                break  # non-retryable error (e.g. 404 model not found), skip to next

    raise RuntimeError(f"All models failed. Last error: {last_exc}") from last_exc


def _download_cv(bucket: str, stored_key: str) -> bytes:
    """Synchronous MinIO download — runs in a thread to avoid blocking the event loop."""
    obj = minio_client.get_object(bucket, stored_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


def _prepare_contents(pdf_bytes: bytes, vacancy: "Vacancy") -> list[Any]:
    """CPU-bound PDF parsing + content building — runs in a thread."""
    cv_text = _extract_text(pdf_bytes)
    if cv_text:
        return _build_text_contents(cv_text, vacancy)
    images = _pdf_to_images(pdf_bytes)
    return _build_image_contents(images, vacancy) if images else []


async def analyze_application(application_id: int) -> None:
    """Entry point for the background task."""
    import asyncio

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not set — skipping analysis for application %d", application_id)
        return

    # ── Phase 1: read everything from DB ────────────────────────────────────
    async with async_session_factory() as session:
        try:
            app_repo = ApplicationRepository(session)
            application: Application | None = await app_repo.get(application_id)
            if not application:
                return

            candidate: Candidate | None = await session.get(Candidate, application.candidate_id)
            vacancy: Vacancy | None = await session.get(Vacancy, application.vacancy_id)
            if not candidate or not vacancy:
                return

            if not candidate.cv_file_id:
                logger.info("Candidate %d has no CV — skipping", candidate.id)
                return

            cv_file: File | None = await session.get(File, candidate.cv_file_id)
            if not cv_file:
                return

            bucket, stored_key = cv_file.bucket, cv_file.stored_key
            candidate_id_log = candidate.id
        except Exception:
            logger.exception("Analysis failed reading DB for application %d", application_id)
            return

    # ── Phase 2: blocking I/O + AI call — in threads, DB session is CLOSED ──
    try:
        pdf_bytes: bytes = await asyncio.to_thread(_download_cv, bucket, stored_key)
        contents: list[Any] = await asyncio.to_thread(_prepare_contents, pdf_bytes, vacancy)

        if not contents:
            logger.warning("Could not render CV pages for candidate %d", candidate_id_log)
            no_text = json.dumps({
                "noTextLayer": True, "summary": "", "strengths": [], "gaps": [],
                "skills": [], "tools": [], "softSkills": [], "certifications": [],
            })
            async with async_session_factory() as session:
                app_repo = ApplicationRepository(session)
                application = await app_repo.get(application_id)
                if application:
                    await app_repo.update(application, {"match_score": 0, "match_summary": no_text})
                    await session.commit()
            return

        result: dict = await asyncio.to_thread(_call_gemini, contents)
    except Exception:
        logger.exception("Analysis failed during CV processing for application %d", application_id)
        return

    # ── Phase 3: write result to DB — fresh session ──────────────────────────
    score = float(result.get("score", 0))
    full_summary = json.dumps(result, ensure_ascii=False)

    async with async_session_factory() as session:
        try:
            app_repo = ApplicationRepository(session)
            application = await app_repo.get(application_id)
            if application:
                await app_repo.update(application, {"match_score": score, "match_summary": full_summary})
                await session.commit()
                logger.info("Analysis complete for application %d — score: %.1f", application_id, score)
        except Exception:
            await session.rollback()
            logger.exception("Analysis failed writing result for application %d", application_id)
