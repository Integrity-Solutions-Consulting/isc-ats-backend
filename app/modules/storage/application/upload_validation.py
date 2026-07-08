"""Content validation for user-uploaded files.

Two defenses, both applied BEFORE the bytes reach object storage:

1. Size cap — uploads are rejected past MAX_UPLOAD_BYTES so a client cannot
   exhaust process memory with a multi-gigabyte body.
2. Magic-byte type check — the real content type is detected from the leading
   bytes (never trusted from the client-supplied Content-Type or extension) and
   must match what the target entity_type is allowed to hold. This blocks
   content smuggling, e.g. an executable renamed to `cv.pdf`.
"""

import io
import zipfile

# 10 MiB — comfortably above a real CV/avatar/promo image, well below a DoS payload.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# Per-entity caps override the global default. CVs and avatars are capped below
# the 10 MiB global: a real CV/avatar is well under 5 MiB, so the lower ceiling
# shrinks the per-request RAM and per-file storage an attacker can force, without
# hurting legitimate uploads.
MAX_BYTES_BY_ENTITY: dict[str, int] = {
    "cv": 5 * 1024 * 1024,
    "avatar": 5 * 1024 * 1024,
}


def max_bytes_for(entity_type: str) -> int:
    """Return the upload size cap for `entity_type` (falls back to the global)."""
    return MAX_BYTES_BY_ENTITY.get(entity_type, MAX_UPLOAD_BYTES)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# entity_type (storage.files vocabulary) → content types it may legitimately hold.
ALLOWED_BY_ENTITY: dict[str, set[str]] = {
    "cv": {"application/pdf"},
    "avatar": {"image/png", "image/jpeg", "image/webp"},
    "vacancy_image": {"image/png", "image/jpeg", "image/webp"},
    "word_doc": {_DOCX_MIME, "application/pdf"},
}


class UploadValidationError(Exception):
    """Base class for rejected uploads."""


class UploadTooLargeError(UploadValidationError):
    """Payload exceeds MAX_UPLOAD_BYTES."""


class UploadTypeError(UploadValidationError):
    """Content type is unrecognized or not allowed for the target entity_type."""


def detect_mime(data: bytes) -> str | None:
    """Return the content type inferred from `data`'s magic bytes, or None.

    Only the formats this application accepts are recognized; everything else
    returns None so the caller can reject it.
    """
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # OOXML (.docx) and other Office files share the ZIP local-file-header magic,
    # so ZIP magic alone is not enough — a generic .zip (or a renamed archive)
    # would be mislabeled as .docx. Only classify as DOCX when the archive
    # actually contains the OOXML wordprocessing structure.
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return None
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return _DOCX_MIME
        return None
    return None


def validate_upload_bytes(entity_type: str, data: bytes) -> str:
    """Validate size and content for `entity_type`; return the detected mime.

    Raises UploadTooLargeError when the payload is too big, and UploadTypeError
    when the real content type is unknown or disallowed for the entity_type.
    """
    limit = max_bytes_for(entity_type)
    if len(data) > limit:
        raise UploadTooLargeError(
            f"File exceeds the maximum allowed size of {limit} bytes"
        )
    detected = detect_mime(data)
    allowed = ALLOWED_BY_ENTITY.get(entity_type, set())
    if detected is None or detected not in allowed:
        raise UploadTypeError(
            f"File content is not a valid type for entity_type '{entity_type}'"
        )
    return detected
