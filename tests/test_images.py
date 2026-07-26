from pathlib import Path

import images

FIXTURE_IMAGE = Path(__file__).parent / "fixtures" / "tiny.png"


def test_file_scheme_reads_local_bytes():
    data, mime = images.fetch_image_bytes(f"file://{FIXTURE_IMAGE}")

    assert data == FIXTURE_IMAGE.read_bytes()
    assert mime == "image/png"


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
