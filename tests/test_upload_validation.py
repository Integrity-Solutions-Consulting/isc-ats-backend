"""Unit tests for upload content validation (size cap + magic-byte type check).

Pure functions — no DB, no MinIO. These guard the file-upload endpoints against
memory-exhaustion DoS (unbounded reads) and content smuggling (a binary renamed
to .pdf). Validation runs BEFORE anything reaches object storage.
"""

import pytest

from app.modules.storage.application.upload_validation import (
    MAX_UPLOAD_BYTES,
    UploadTooLargeError,
    UploadTypeError,
    detect_mime,
    validate_upload_bytes,
)

# Minimal valid signatures — the detector only inspects the leading bytes.
_PDF = b"%PDF-1.4\n%%EOF"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
_DOCX = b"PK\x03\x04" + b"\x00" * 16
_GARBAGE = b"MZ\x90\x00garbage-exe-bytes"  # PE/.exe header


def test_detect_mime_recognizes_known_signatures() -> None:
    assert detect_mime(_PDF) == "application/pdf"
    assert detect_mime(_PNG) == "image/png"
    assert detect_mime(_JPEG) == "image/jpeg"
    assert detect_mime(_WEBP) == "image/webp"
    assert (
        detect_mime(_DOCX)
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_detect_mime_returns_none_for_unknown() -> None:
    assert detect_mime(_GARBAGE) is None
    assert detect_mime(b"") is None


def test_validate_accepts_pdf_for_cv() -> None:
    assert validate_upload_bytes("cv", _PDF) == "application/pdf"


def test_validate_accepts_image_for_avatar() -> None:
    assert validate_upload_bytes("avatar", _PNG) == "image/png"
    assert validate_upload_bytes("avatar", _JPEG) == "image/jpeg"


def test_validate_accepts_image_for_vacancy_image() -> None:
    assert validate_upload_bytes("vacancy_image", _WEBP) == "image/webp"


def test_validate_rejects_pdf_smuggled_as_avatar() -> None:
    # A PDF tagged as an avatar must fail — entity_type and content must agree.
    with pytest.raises(UploadTypeError):
        validate_upload_bytes("avatar", _PDF)


def test_validate_rejects_unknown_content_for_cv() -> None:
    # An .exe renamed to .pdf has no PDF signature → rejected.
    with pytest.raises(UploadTypeError):
        validate_upload_bytes("cv", _GARBAGE)


def test_validate_rejects_oversized_payload() -> None:
    too_big = _PDF + b"\x00" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UploadTooLargeError):
        validate_upload_bytes("cv", too_big)


def test_validate_accepts_payload_at_the_limit() -> None:
    at_limit = _PDF + b"\x00" * (MAX_UPLOAD_BYTES - len(_PDF))
    assert len(at_limit) == MAX_UPLOAD_BYTES
    assert validate_upload_bytes("cv", at_limit) == "application/pdf"
