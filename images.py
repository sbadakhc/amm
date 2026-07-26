"""
Shared image-fetching helper for `file://` (local dev/demo) and `s3://` (production,
§3.1) image URLs. Used by Evidence Agent and Consistency Agent, which both need raw
image bytes for their respective model calls.

S3 access works against real AWS or any S3-compatible store (MinIO, R2, etc.) --
set S3_ENDPOINT_URL for the latter; leave it unset for real AWS's default credential
and endpoint resolution. See docs/decisions/0006-s3-storage-self-hosted-minio.md.
"""

import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _s3_client


def fetch_image_bytes(url: str) -> tuple[bytes, str]:
    """Returns (raw bytes, mime type) for a file:// or s3:// image URL."""
    parsed = urlparse(url)
    if parsed.scheme == "file":
        path = Path(parsed.path)
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    elif parsed.scheme == "s3":
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        obj = _get_s3_client().get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        mime = obj.get("ContentType") or mimetypes.guess_type(key)[0] or "image/png"
    else:
        raise ValueError(f"Unsupported image URL scheme: {url}")
    return data, mime
