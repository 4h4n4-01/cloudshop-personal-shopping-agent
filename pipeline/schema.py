"""Silver-layer schema: one clean, typed product in the unified catalog shape.

This is the Medallion *silver contract*. Soft cleaning (filtering list fields to the
controlled vocabularies) happens in `silver.py` so it can be reported; this model enforces
the *hard* constraints and coerces types. A row that cannot satisfy them is dropped by
silver, with a reason.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, field_validator

from app.models import CATEGORIES
from app.models import COLORS, OCCASIONS, STYLES  # controlled vocabularies, re-exported for silver

from .normalize import _MATERIAL_WORDS

MATERIALS = sorted(set(_MATERIAL_WORDS.values()))


class Product(BaseModel):
    id: str
    name: str
    brand: str
    section: Literal["women", "men", "unisex"]
    category: str
    price: float
    colors: list[str] = []
    materials: list[str] = []
    style_tags: list[str] = []
    occasions: list[str] = []
    fit: str = "regular"
    size_system: str = "alpha"
    stock: dict[str, int] = {}
    description: str = ""
    # Richer attributes the Verifier (Idea 2) can ground rationale claims against.
    fabric_composition: str = ""      # e.g. "55% linen, 45% viscose"
    care: str = ""                    # e.g. "Machine wash cold", "Dry clean only"
    origin: str = ""                  # country of manufacture
    returnable: bool = True           # False = final sale, no returns
    rating: Optional[float] = None    # 1-5 average
    review_count: int = 0
    original_price: Optional[float] = None  # present when discounted; > price
    # Online-store-style fields (synthetic multi-retailer catalog).
    store: str = ""                   # the (synthetic) retailer carrying the item
    badges: list[str] = []            # e.g. "Bestseller", "New arrival", "Limited edition"
    free_shipping: bool = False
    colorways: list[str] = []         # human-readable colour options shown in the listing
    # Fashion attributes a shopper filters by on real brand sites (category-appropriate; "" = n/a).
    silhouette: str = ""              # garment cut: peplum / wrap / a-line / bodycon / chelsea / tote …
    sleeve_length: str = ""           # sleeveless / short sleeve / long sleeve / three-quarter / cap
    neckline: str = ""                # crew / v-neck / scoop / collared / turtleneck / halter / boat
    pattern: str = ""                 # solid / striped / floral / checked / polka dot / graphic / plaid
    hem_length: str = ""              # mini / midi / maxi (dresses, skirts)
    rise: str = ""                    # high-rise / mid-rise / low-rise (bottoms)
    heel_height: str = ""             # flat / low / mid / high (shoes)

    @field_validator("price")
    @classmethod
    def _price_positive(cls, v: float) -> float:
        if v is None or float(v) <= 0:
            raise ValueError("price must be > 0")
        return round(float(v), 2)

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"unknown category {v!r}")
        return v

    @field_validator("stock")
    @classmethod
    def _clean_stock(cls, v: dict) -> dict:
        return {str(s): max(int(q), 0) for s, q in (v or {}).items()}
