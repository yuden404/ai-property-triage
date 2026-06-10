"""Generate synthetic property listings for the Knowledge Base (spec: ≥20).

Gemini generates a varied mix of residential + commercial listings. Each
listing is written as ONE .txt file (one file = one retrievable comparable)
into code/rag_service/listings_data/.

Run:  AWS_PROFILE=course .venv/bin/python code/rag_service/scripts/01_generate_listings.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))  # → code/ on the path

from shared.gemini_utils import generate  # noqa: E402

OUT_DIR = HERE.parents[1] / "listings_data"
TARGET = 24  # a few above the required 20

PROMPT = f"""\
You are generating a synthetic dataset for a real-estate agency's knowledge base.

Create exactly {TARGET} fictional Israeli property listings as a JSON array.
Mix: ~14 residential (apartment / house / villa) and ~10 commercial
(office / retail / industrial), spread across Israeli cities (Tel Aviv,
Ramat Gan, Haifa, Jerusalem, Beer Sheva, Netanya, Herzliya, Holon, etc.),
with varied prices, sizes and conditions (some pristine, some needing renovation).

Each array item must be an object with EXACTLY these fields:
- "id": "L001".."L{TARGET:03d}" (sequential)
- "title": short headline, e.g. "Sunny 3BR apartment near Bialik Park"
- "property_type": one of apartment|house|villa|office|retail|industrial
- "location": city + neighborhood
- "price_ils": integer
- "rooms": number (residential) or 0 (commercial open space)
- "size_sqm": integer
- "condition_score": integer 1-5 (1=needs major renovation, 5=pristine)
- "features": array of 3-6 short strings (e.g. "balcony", "parking", "elevator")
- "description": 2-3 natural sentences a listing agent would write, consistent
  with all the fields above (mention price, size and condition naturally).

Return ONLY the JSON array — no markdown fences, no commentary.
"""


def listing_to_text(it: dict) -> str:
    """Render one listing as the .txt document that gets embedded in the KB."""
    return (
        f"Listing {it['id']}: {it['title']}\n"
        f"Type: {it['property_type']}\n"
        f"Location: {it['location']}\n"
        f"Price: {it['price_ils']:,} ILS\n"
        f"Rooms: {it['rooms']}\n"
        f"Size: {it['size_sqm']} sqm\n"
        f"Condition score: {it['condition_score']}/5\n"
        f"Features: {', '.join(it['features'])}\n"
        f"Description: {it['description']}\n"
    )


def main() -> int:
    print(f"Asking Gemini for {TARGET} synthetic listings…")
    raw = generate(PROMPT, temperature=0.9)
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    items = json.loads(raw)
    assert isinstance(items, list) and len(items) >= 20, f"expected ≥20 listings, got {len(items)}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for it in items:
        (OUT_DIR / f"{it['id']}.txt").write_text(listing_to_text(it), encoding="utf-8")

    types = sorted({it["property_type"] for it in items})
    print(f"OK: wrote {len(items)} listings to {OUT_DIR}")
    print(f"    types: {types}")
    print(f"    sample:\n{listing_to_text(items[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
