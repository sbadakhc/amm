"""
Intake Agent — maps a raw listings row to the canonical document every other agent
consumes. See SPEC.md §3.1. Pure data mapping, no model call.
"""


def to_canonical_document(row: dict) -> dict:
    return {
        "listingId": row["listing_id"],
        "title": row["title"],
        "description": row["description"],
        "images": [img["url"] for img in row["images"]],
        "sellerId": row["seller"]["sellerId"],
        "sellerVerified": row["seller"]["verified"],
        "sellerPreviousViolations": row["seller"]["previousViolations"],
        "categoryId": row["category"]["id"],
        "declaredBrand": row["brand"],
        "condition": row["condition"],
    }
