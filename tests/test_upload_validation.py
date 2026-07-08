"""Unit tests for upload content validation (size cap + magic-byte type check).

Pure functions — no DB, no MinIO. These guard the file-upload endpoints against
memory-exhaustion DoS (unbounded reads) and content smuggling (a binary renamed
to .pdf). Validation runs BEFORE anything reaches object storage.
"""

import io
import zipfile

import pytest

from app.modules.storage.application.upload_validation import (
    UploadTooLargeError,
    UploadTypeError,
    detect_mime,
    max_bytes_for,
    validate_upload_bytes,
)

# Minimal valid signatures — the detector only inspects the leading bytes.
_PDF = b"%PDF-1.4\n%%EOF"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
_GARBAGE = b"MZ\x90\x00garbage-exe-bytes"  # PE/.exe header


def _make_docx() -> bytes:
    """A minimal but structurally valid .docx (OOXML) zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<document/>")
    return buf.getvalue()


def _make_plain_zip() -> bytes:
    """A generic zip archive that shares ZIP magic but is NOT a .docx."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notes.txt", "hello")
    return buf.getvalue()


_DOCX = _make_docx()
_PLAIN_ZIP = _make_plain_zip()


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


def test_detect_mime_rejects_plain_zip_as_docx() -> None:
    """A generic .zip shares ZIP magic but lacks the OOXML entries → not docx."""
    assert detect_mime(_PLAIN_ZIP) is None


def test_detect_mime_rejects_truncated_zip() -> None:
    """Bare ZIP magic with no valid archive body must not be classified as docx."""
    assert detect_mime(b"PK\x03\x04" + b"\x00" * 16) is None


def test_validate_rejects_plain_zip_for_word_doc() -> None:
    with pytest.raises(UploadTypeError):
        validate_upload_bytes("word_doc", _PLAIN_ZIP)


def test_validate_accepts_real_docx_for_word_doc() -> None:
    assert (
        validate_upload_bytes("word_doc", _DOCX)
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


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
    cap = max_bytes_for("cv")
    too_big = _PDF + b"\x00" * (cap + 1)
    with pytest.raises(UploadTooLargeError):
        validate_upload_bytes("cv", too_big)


def test_validate_accepts_payload_at_the_limit() -> None:
    cap = max_bytes_for("cv")
    at_limit = _PDF + b"\x00" * (cap - len(_PDF))
    assert len(at_limit) == cap
    assert validate_upload_bytes("cv", at_limit) == "application/pdf"
