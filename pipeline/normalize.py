"""Data-normalisation helpers for the silver layer (category scope rules, materials vocabulary,
size-system inference). Self-contained in the pipeline so the data build has no dependency on the
app's store code."""
import re

from app.models import BOTTOM_CATEGORIES, ONE_SIZE_CATEGORIES, SHOE_CATEGORIES

# Out-of-scope accessories that mention a shoe/bag but aren't apparel, and children's items.
_NOT_PRODUCT = re.compile(
    r"\b(shoe ?care|shoe ?trees?|shoe ?horns?|insoles?|shoelaces?|socks?|gift ?(card|wrap)|"
    r"care kit|bag (charm|strap)s?|dust ?bags?|key ?chains?|wallets?|card (holder|sleeve)s?|passport)\b")
KIDS = re.compile(r"\b(kids?|kid's|baby|babies|toddlers?|boys|girls|junior|infants?|children|child)\b")

# Fibre words -> canonical material (used to validate the controlled materials vocabulary).
_MATERIAL_WORDS = {
    "cotton": "cotton", "linen": "linen", "hemp": "hemp", "wool": "wool", "merino": "wool",
    "cashmere": "cashmere", "silk": "silk", "polyester": "polyester", "nylon": "nylon", "polyamide": "nylon",
    "elastane": "elastane", "spandex": "elastane", "lycra": "elastane", "viscose": "viscose", "rayon": "viscose",
    "modal": "modal", "tencel": "lyocell", "lyocell": "lyocell", "acrylic": "acrylic", "leather": "leather",
}


def size_system(category: str, stock: dict) -> str:
    if category in SHOE_CATEGORIES:
        return "shoe"
    if category in ONE_SIZE_CATEGORIES:
        return "one-size"
    numeric = [s for s in stock if s.isdigit() and 24 <= int(s) <= 44]
    return "waist" if category in BOTTOM_CATEGORIES and numeric else "alpha"
