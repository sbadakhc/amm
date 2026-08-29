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

# docs/decisions/0033: image URLs come from listings.images, seller-controlled data
# with no schema-level restriction on scheme/path/bucket -- fetch_image_bytes must
# not trust them blindly. file:// is explicitly local dev/demo only (SPEC.md §3.1);
# default root is this project's own demo images directory. Comma-separated to allow
# more than one (e.g. adding a test-fixtures root without widening the real default).
LOCAL_IMAGE_ROOTS = [
    Path(p).resolve()
    for p in os.environ.get("LOCAL_IMAGE_ROOTS", str(Path(__file__).parent / "images")).split(",")
    if p
]

# Opt-in, not opt-out: which S3 bucket(s) are legitimate is deployment-specific and
# not knowable from code alone, so an empty allowlist means "not enforced" (matches
# pre-fix behavior) rather than guessing wrong and breaking real deployments. Set
# this in production to actually close the gap.
S3_ALLOWED_BUCKETS = {b for b in os.environ.get("S3_ALLOWED_BUCKETS", "").split(",") if b}

_s3_client = None


def _validate_local_path(path: Path) -> Path:
    """Resolves `path` and confirms it's inside one of LOCAL_IMAGE_ROOTS -- blocks
    `file://` from reading arbitrary local files (docs/decisions/0033) via a listing
    whose images[].url a seller fully controls. Symlink-safe: `.resolve()` follows
    symlinks before the containment check, so a symlink inside an allowed root
    pointing outside it is still caught."""
    resolved = path.resolve()
    for root in LOCAL_IMAGE_ROOTS:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        f"file:// path {path} resolves outside the allowed local image root(s) "
        f"{[str(r) for r in LOCAL_IMAGE_ROOTS]} -- refusing to read it"
    )


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
        path = _validate_local_path(Path(parsed.path))
        data = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    elif parsed.scheme == "s3":
        bucket = parsed.netloc
        if S3_ALLOWED_BUCKETS and bucket not in S3_ALLOWED_BUCKETS:
            raise ValueError(f"s3:// bucket {bucket!r} is not in S3_ALLOWED_BUCKETS -- refusing to read it")
        key = parsed.path.lstrip("/")
        obj = _get_s3_client().get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        mime = obj.get("ContentType") or mimetypes.guess_type(key)[0] or "image/png"
    else:
        raise ValueError(f"Unsupported image URL scheme: {url}")
    return data, mime
