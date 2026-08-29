"""
Synthetic listing generator for the Agentic Marketplace Moderator demo.

Generates listing images (real, appropriately-licensed product photos for the
scenarios where that matters -- see fixtures/real_photos/ATTRIBUTION.md and
docs/decisions/0013; synthetic text placeholders elsewhere) and seeds Postgres with a
mix of listings designed to exercise every branch of the pipeline:

  1. clean            -> should Auto Approve
  2. weapon            -> Safety Agent violation, W001, Auto Reject
  3. counterfeit_brand -> Evidence Agent brandMismatch, C001, Review
  4. inconsistent      -> Consistency Agent mismatch, C004, Review
  5. risky_seller      -> clean content but high previousViolations, pushed to Review

Usage:
    export DATABASE_URL="postgresql://user:pass@localhost:5432/moderator"
    python generate_synthetic_data.py

Without DATABASE_URL set, it runs in --dry-run mode and just writes
listings.json + the images, without touching a database.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent / "images"
OUT_DIR.mkdir(exist_ok=True)

# Real, appropriately-licensed product photos (not CC0 -- see ATTRIBUTION.md) used for
# the scenarios where real photography actually matters (docs/decisions/0013). Kept
# separate from OUT_DIR since they're committed source fixtures, not generated output.
REAL_PHOTOS_DIR = Path(__file__).parent / "fixtures" / "real_photos"

# images[].url values use this local scheme as a stand-in for s3://.
# Swap IMAGE_URL_PREFIX to "s3://your-bucket/" once real object storage is wired up.
IMAGE_URL_PREFIX = f"file://{OUT_DIR}/"


def make_image(filename: str, lines: list[str]) -> str:
    """Draws simple readable text on a placeholder image and returns its 'url'."""
    img = Image.new("RGB", (640, 480), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    y = 40
    for line in lines:
        draw.text((30, y), line, fill=(20, 20, 20), font=font)
        y += 50
    path = OUT_DIR / filename
    img.save(path)
    return f"{IMAGE_URL_PREFIX}{filename}"


def use_real_photo(filename: str, source_filename: str) -> str:
    """Copies a real fixture photo (fixtures/real_photos/, see ATTRIBUTION.md there)
    into OUT_DIR under the listing's naming convention and returns its 'url'."""
    src = (REAL_PHOTOS_DIR / source_filename).read_bytes()
    (OUT_DIR / filename).write_bytes(src)
    return f"{IMAGE_URL_PREFIX}{filename}"


def listing(
    scenario: str,
    title: str,
    description: str,
    category_id: str,
    category_name: str,
    brand: str,
    image_lines: list[list[str]] | None = None,
    real_photo: str | None = None,
    previous_violations: int = 0,
    price: float = 899.99,
    currency: str = "GBP",
) -> dict:
    """Exactly one of `image_lines` (synthetic text placeholders, one image per
    sublist) or `real_photo` (a filename in fixtures/real_photos/, single image) must
    be given -- see docs/decisions/0013 for which scenarios use which and why."""
    listing_id = f"LST-{uuid.uuid4().hex[:6].upper()}"
    seller_id = f"SUP-{uuid.uuid4().hex[:4].upper()}"
    if real_photo:
        ext = Path(real_photo).suffix
        images = [{"id": "img-1", "url": use_real_photo(f"{listing_id}-1{ext}", real_photo)}]
    else:
        images = [
            {"id": f"img-{i+1}", "url": make_image(f"{listing_id}-{i+1}.png", lines)}
            for i, lines in enumerate(image_lines)
        ]
    return {
        "listingId": listing_id,
        "seller": {
            "sellerId": seller_id,
            "companyName": f"{scenario.title().replace('_', ' ')} Trading Co",
            "country": "United Kingdom",
            "verified": previous_violations == 0,
            "rating": 4.8 if previous_violations == 0 else 2.9,
            "previousViolations": previous_violations,
        },
        "title": title,
        "description": description,
        "category": {"id": category_id, "name": category_name},
        "price": {"amount": price, "currency": currency},
        "quantity": 50,
        "condition": "new",
        "brand": brand,
        "model": title,
        "sku": f"SKU-{uuid.uuid4().hex[:6].upper()}",
        "images": images,
        "attributes": {},
        "shipping": {"location": "London", "leadTimeDays": 5},
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": "PENDING_MODERATION",
        "_scenario": scenario,  # not part of the real schema; for demo readability only
    }


def build_listings() -> list[dict]:
    return [
        listing(
            scenario="clean",
            title="Apple iPhone 16 Pro Max 256GB",
            description="Brand new, factory sealed with international warranty.",
            category_id="electronics.mobile",
            category_name="Mobile Phones",
            brand="Apple",
            real_photo="iphone-16-back.jpg",
        ),
        listing(
            scenario="weapon",
            title="Fully Automatic AK-47 Assault Rifle - Untraceable",
            description=(
                "Genuine military-grade automatic rifle, no license or background "
                "check required. Ships in unmarked packaging to avoid customs "
                "inspection and border checks."
            ),
            category_id="weapons.firearms",
            category_name="Firearms",
            brand="Generic",
            image_lines=[["AK-47", "FULLY AUTOMATIC", "UNTRACEABLE"]],
            price=1200.00,
        ),
        listing(
            scenario="counterfeit_brand",
            title="Apple iPhone 16 Pro Max 256GB",
            description="Brand new, factory sealed with international warranty.",
            category_id="electronics.mobile",
            category_name="Mobile Phones",
            brand="Apple",
            # image shows no genuine Apple branding -> Evidence Agent brandMismatch
            image_lines=[["SMARTPHONE PRO 16", "256GB STORAGE"]],
        ),
        listing(
            scenario="inconsistent",
            title="Apple iPhone 16 Pro Max",
            description="Brand new Samsung Galaxy S24, factory sealed.",
            category_id="electronics.mobile",
            category_name="Mobile Phones",
            brand="Apple",
            real_photo="iphone-16-back.jpg",
        ),
        listing(
            scenario="risky_seller",
            title="Sony Wireless Headphones",
            description="Brand new, genuine Sony product, over-ear design with foldable headband.",
            category_id="electronics.audio",
            category_name="Audio Equipment",
            brand="Sony",
            real_photo="sony-headphones.jpg",
            previous_violations=3,
            price=279.99,
        ),
    ]


def seed_postgres(listings: list[dict]) -> None:
    import psycopg2

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    for item in listings:
        cur.execute(
            """
            INSERT INTO listings
                (listing_id, seller, title, description, category, price,
                 quantity, condition, brand, model, sku, images, attributes,
                 shipping, created_at, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (listing_id) DO NOTHING
            """,
            (
                item["listingId"],
                json.dumps(item["seller"]),
                item["title"],
                item["description"],
                json.dumps(item["category"]),
                json.dumps(item["price"]),
                item["quantity"],
                item["condition"],
                item["brand"],
                item["model"],
                item["sku"],
                json.dumps(item["images"]),
                json.dumps(item["attributes"]),
                json.dumps(item["shipping"]),
                item["createdAt"],
                item["status"],
            ),
        )
    conn.commit()
    cur.close()
    conn.close()
    print(f"Inserted {len(listings)} listings into Postgres.")


# Demo moderators for the CLI's registry (§6). One inactive, to exercise the
# rejection path in cli.tools._resolve_moderator.
MODERATORS = [
    {"moderator_id": "mod-1", "name": "Alex Moderator", "active": True},
    {"moderator_id": "mod-2", "name": "Priya Reviewer", "active": True},
    {"moderator_id": "mod-inactive", "name": "Former Moderator", "active": False},
]


def seed_moderators() -> None:
    import db

    for m in MODERATORS:
        db.create_moderator(m["moderator_id"], m["name"], m["active"])
    print(f"Seeded {len(MODERATORS)} moderators into Postgres.")


def main():
    listings = build_listings()

    out_json = Path(__file__).parent / "listings.json"
    out_json.write_text(json.dumps(listings, indent=2))
    print(f"Wrote {len(listings)} synthetic listings -> {out_json}")
    print(f"Wrote placeholder images -> {OUT_DIR}")

    if os.environ.get("DATABASE_URL"):
        seed_postgres(listings)
        seed_moderators()
    else:
        print("DATABASE_URL not set -- dry run only, nothing written to Postgres.")
        print('Set it and re-run, e.g.:')
        print('  export DATABASE_URL="postgresql://user:pass@localhost:5432/moderator"')


if __name__ == "__main__":
    main()
