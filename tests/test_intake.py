from intake import to_canonical_document


def test_maps_raw_row_to_canonical_document():
    row = {
        "listing_id": "LST-100234",
        "title": "Apple iPhone 16 Pro Max 256GB",
        "description": "Brand new, factory sealed with international warranty.",
        "images": [{"id": "img-1", "url": "s3://listings/img1.jpg"}, {"id": "img-2", "url": "s3://listings/img2.jpg"}],
        "seller": {"sellerId": "SUP-9281", "verified": True, "previousViolations": 0},
        "category": {"id": "electronics.mobile", "name": "Mobile Phones"},
        "brand": "Apple",
        "condition": "new",
    }

    doc = to_canonical_document(row)

    assert doc == {
        "listingId": "LST-100234",
        "title": "Apple iPhone 16 Pro Max 256GB",
        "description": "Brand new, factory sealed with international warranty.",
        "images": ["s3://listings/img1.jpg", "s3://listings/img2.jpg"],
        "sellerId": "SUP-9281",
        "sellerVerified": True,
        "sellerPreviousViolations": 0,
        "categoryId": "electronics.mobile",
        "declaredBrand": "Apple",
        "condition": "new",
    }
