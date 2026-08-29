"""
Regression tests for docs/decisions/0034 (found via /code-review): a failed image
fetch (images.fetch_image_bytes raising -- ValueError from docs/decisions/0033's
path/bucket allowlisting, or any other error, e.g. a stale reference to a deleted
file) used to propagate unhandled through scripts/inspect_listing.py, crashing the
whole single-listing inspection or queue survey over one bad image instead of
skipping just that image and reporting it.
"""

from scripts import inspect_listing


def test_fetch_images_to_temp_skips_blocked_image_and_reports_it(monkeypatch, capsys):
    monkeypatch.setattr(
        inspect_listing,
        "get_listing",
        lambda listing_id: {"listing": {"images": [{"id": "img-1", "url": "file:///etc/hostname"}]}},
    )

    def _raise(url):
        raise ValueError(f"file:// path {url} resolves outside the allowed local image root(s)")

    monkeypatch.setattr(inspect_listing, "fetch_image_bytes", _raise)

    paths = inspect_listing.fetch_images_to_temp("LST-TEST")

    assert paths == []
    assert "unavailable" in capsys.readouterr().out


def test_fetch_images_to_temp_continues_past_a_blocked_image(monkeypatch, tmp_path):
    """One blocked image among several must not prevent the others from being
    fetched -- the bug this regression test covers was a hard crash, not just a
    missing image."""
    monkeypatch.setattr(
        inspect_listing,
        "get_listing",
        lambda listing_id: {
            "listing": {
                "images": [
                    {"id": "img-1", "url": "file:///etc/hostname"},
                    {"id": "img-2", "url": "file:///ok.png"},
                ]
            }
        },
    )

    def _fetch(url):
        if "etc/hostname" in url:
            raise ValueError("blocked")
        return b"fake-image-bytes", "image/png"

    monkeypatch.setattr(inspect_listing, "fetch_image_bytes", _fetch)

    paths = inspect_listing.fetch_images_to_temp("LST-TEST")

    assert len(paths) == 1
    assert paths[0].endswith(".png")


def test_print_queue_table_flags_blocked_image_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr(
        inspect_listing.db,
        "list_listings_by_status",
        lambda status: (
            [
                {
                    "listing_id": "LST-TEST",
                    "title": "Test",
                    "status": status,
                    "created_at": "2026-01-01T00:00:00Z",
                    "images": [{"id": "img-1", "url": "file:///etc/hostname"}],
                }
            ]
            if status == "PENDING_REVIEW"
            else []
        ),
    )
    monkeypatch.setattr(inspect_listing.db, "latest_artifact", lambda listing_id, agent: None)
    monkeypatch.setattr(inspect_listing, "fetch_image_bytes", lambda url: (_ for _ in ()).throw(ValueError("blocked")))
    monkeypatch.setattr(inspect_listing, "start_server", lambda directory: 12345)

    inspect_listing.print_queue_table(statuses=["PENDING_REVIEW"])

    out = capsys.readouterr().out
    assert "LST-TEST" in out
    assert "unavailable" in out
