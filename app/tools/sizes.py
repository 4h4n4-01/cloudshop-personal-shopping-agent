"""Shoe sizes. Stores label them EU, UK or US (or a bare number), and shoppers say whichever
they know, so both sides are converted to EU before comparing.

Conversions are the usual approximations: EU = UK + 33, and US is UK + 1 for men's (and
unisex, since sneakers are sized on the men's scale) or UK + 2 for women's. They can be half
a size off for some brands, so the Verifier always shows the conversion it used.
"""
import re
from typing import Optional


def parse(label) -> Optional[tuple[str, float]]:
    """'EU 38' -> ('eu', 38.0); '38' -> ('eu', 38.0); 'US 8.5' -> ('us', 8.5); 'M' -> None.
    Sneaker shops' dual US labels read as ('usm', 10.0)."""
    text = str(label or "").lower()
    m = re.match(r"^\s*(?:us\s*)?m\s*(\d{1,2}(?:[.,]5)?)\s*/\s*w\s*\d{1,2}(?:[.,]5)?\s*$", text)
    if m:
        return "usm", float(m.group(1).replace(",", "."))
    m = re.match(r"^\s*(eu|uk|us)?\s*(\d{1,2}(?:[.,]5)?)\s*$", text)
    if not m:
        return None
    system, n = m.group(1), float(m.group(2).replace(",", "."))
    if system is None:
        if n < 30:  # a bare "8" could be UK or US
            return None
        system = "eu"
    return system, n


def to_eu(label, section: Optional[str] = None) -> Optional[float]:
    p = parse(label)
    if not p:
        return None
    system, n = p
    if system == "eu":
        return n
    if system == "usm":  # labelled on the men's scale whatever the section
        return n - 1 + 33
    uk = n if system == "uk" else n - (2 if section == "women" else 1)
    return uk + 33


def label(value) -> str:
    """A store's variant value as a size label: '40' -> 'EU 40', 'us 8' -> 'US 8'."""
    p = parse(value)
    if not p or p[0] == "usm":
        return str(value).strip()
    return f"{p[0].upper()} {p[1]:g}"


def pick(stock: dict, wanted: str, wanted_section: Optional[str], product_section: Optional[str]) -> Optional[str]:
    """The store's size label nearest the shopper's size, within half a size. Among equally
    near labels, prefer one in stock."""
    target = to_eu(wanted, wanted_section)
    if target is None:
        return None
    best = None
    for key, qty in stock.items():
        eu = to_eu(key, product_section)
        if eu is None or abs(eu - target) > 0.5:
            continue
        rank = (abs(eu - target), -qty)
        if best is None or rank < best[0]:
            best = (rank, key)
    return best[1] if best else None


def explain(wanted: str, key: Optional[str], wanted_section: Optional[str],
            product_section: Optional[str]) -> tuple[str, bool]:
    """('You said UK 5 ≈ EU 38; this store lists it as EU 38', exact?)"""
    target = to_eu(wanted, wanted_section)
    if target is None:
        return f"Couldn't tell which size system '{wanted}' is (EU, UK or US)", False
    said = f"You said {wanted.upper()}" + ("" if (parse(wanted) or ("eu",))[0] == "eu" else f" ≈ EU {target:g}")
    if not key:
        return f"{said}; no nearby size is listed", False
    eu = to_eu(key, product_section)
    exact = eu == target
    listed = f"this store lists {key}" + ("" if (parse(key) or ("eu",))[0] == "eu" or eu is None else f" ≈ EU {eu:g}")
    return f"{said}; {listed}" + ("" if exact else " (half a size off)"), exact
