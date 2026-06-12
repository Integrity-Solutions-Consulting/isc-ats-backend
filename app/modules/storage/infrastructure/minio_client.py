import io
import logging
import os
import uuid

from minio import Minio

from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialize MinIO client.
# TLS is controlled by settings.minio_secure — False by default to keep local
# docker-compose working; set MINIO_SECURE=true in production.
minio_client = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    secure=settings.minio_secure,
)


def init_minio() -> None:
    """Ensure the bucket configured for candidate CVs exists in MinIO."""
    try:
        bucket = settings.minio_bucket
        if not minio_client.bucket_exists(bucket):
            minio_client.make_bucket(bucket)
            logger.info("Created MinIO bucket: %s", bucket)
        else:
            logger.info("MinIO bucket '%s' already exists.", bucket)
    except Exception as e:
        logger.warning("Failed to initialize MinIO bucket: %s", e)

def upload_file_to_minio(file_data: bytes, file_name: str, content_type: str | None) -> str:
    """Uploads binary file data to the MinIO bucket and returns a unique stored_key."""
    ext = os.path.splitext(file_name)[1]
    stored_key = f"{uuid.uuid4()}{ext}"
    
    bucket = settings.minio_bucket
    data_stream = io.BytesIO(file_data)
    
    minio_client.put_object(
        bucket,
        stored_key,
        data_stream,
        length=len(file_data),
        content_type=content_type or "application/octet-stream"
    )
    
    return stored_key
