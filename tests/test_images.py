from pathlib import Path

import pytest

import images

FIXTURE_IMAGE = Path(__file__).parent / "fixtures" / "tiny.png"
# tests/conftest.py's autouse _allow_test_fixture_images_root fixture already scopes
# images.LOCAL_IMAGE_ROOTS to tests/fixtures/ for every test in this suite -- the two
# path-blocking tests below rely on that same scoping (their target paths are outside
# tests/fixtures/, so they're expected to be blocked without any extra setup here).


def test_file_scheme_reads_local_bytes():
    data, mime = images.fetch_image_bytes(f"file://{FIXTURE_IMAGE}")

    assert data == FIXTURE_IMAGE.read_bytes()
    assert mime == "image/png"


def test_file_scheme_blocks_path_outside_allowed_roots():
    """docs/decisions/0033: a listing's images[].url is seller-controlled with no
    schema-level restriction -- confirmed live against a real path (/etc/hostname)
    before this fix that fetch_image_bytes would read arbitrary local files."""
    with pytest.raises(ValueError, match="outside the allowed local image root"):
        images.fetch_image_bytes("file:///etc/hostname")


def test_file_scheme_blocks_traversal_out_of_allowed_root():
    outside_path = (FIXTURE_IMAGE.parent / ".." / ".." / ".env").resolve()
    with pytest.raises(ValueError, match="outside the allowed local image root"):
        images.fetch_image_bytes(f"file://{outside_path}")


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class _FakeS3Client:
    def __init__(self, data: bytes, content_type: str | None = None):
        self._data = data
        self._content_type = content_type
        self.calls = []

    def get_object(self, Bucket, Key):
        self.calls.append((Bucket, Key))
        obj = {"Body": _FakeBody(self._data)}
        if self._content_type:
            obj["ContentType"] = self._content_type
        return obj


def test_s3_scheme_fetches_via_client(monkeypatch):
    fake_bytes = FIXTURE_IMAGE.read_bytes()
    fake_client = _FakeS3Client(fake_bytes, content_type="image/png")
    monkeypatch.setattr(images, "_get_s3_client", lambda: fake_client)

    data, mime = images.fetch_image_bytes("s3://amm-listings/LST-041BC6-1.png")

    assert data == fake_bytes
    assert mime == "image/png"
    assert fake_client.calls == [("amm-listings", "LST-041BC6-1.png")]


def test_s3_scheme_falls_back_to_guessed_mime_without_content_type(monkeypatch):
    fake_client = _FakeS3Client(b"data", content_type=None)
    monkeypatch.setattr(images, "_get_s3_client", lambda: fake_client)

    _, mime = images.fetch_image_bytes("s3://bucket/photo.jpg")

    assert mime == "image/jpeg"


def test_unsupported_scheme_raises():
    import pytest

    with pytest.raises(ValueError):
        images.fetch_image_bytes("https://example.com/image.png")


def test_s3_scheme_unrestricted_when_allowlist_empty(monkeypatch):
    """docs/decisions/0033: S3_ALLOWED_BUCKETS is opt-in -- an empty allowlist (the
    default) doesn't restrict anything, matching pre-fix behavior. Bucket allowlisting
    is a deployment-specific defense-in-depth knob, not a default-on restriction like
    the file:// fix, since the "right" bucket isn't knowable from code alone."""
    monkeypatch.setattr(images, "S3_ALLOWED_BUCKETS", set())
    fake_client = _FakeS3Client(b"data", content_type="image/png")
    monkeypatch.setattr(images, "_get_s3_client", lambda: fake_client)

    images.fetch_image_bytes("s3://any-bucket-at-all/photo.jpg")  # does not raise


def test_s3_scheme_blocks_bucket_outside_allowlist(monkeypatch):
    monkeypatch.setattr(images, "S3_ALLOWED_BUCKETS", {"amm-listings"})
    fake_client = _FakeS3Client(b"data", content_type="image/png")
    monkeypatch.setattr(images, "_get_s3_client", lambda: fake_client)

    with pytest.raises(ValueError, match="not in S3_ALLOWED_BUCKETS"):
        images.fetch_image_bytes("s3://some-other-bucket/photo.jpg")
    assert fake_client.calls == []  # never reached the network call


def test_s3_scheme_allows_bucket_in_allowlist(monkeypatch):
    monkeypatch.setattr(images, "S3_ALLOWED_BUCKETS", {"amm-listings"})
    fake_client = _FakeS3Client(b"data", content_type="image/png")
    monkeypatch.setattr(images, "_get_s3_client", lambda: fake_client)

    images.fetch_image_bytes("s3://amm-listings/photo.jpg")  # does not raise
