"""Silver layer: cleaning and standardisation.

Takes raw bronze rows and produces clean, typed products in one unified shape:
  * soft clean  - keep only in-vocabulary colours / materials / styles / occasions,
                  derive size_system when missing, de-duplicate by id
  * scope filter - drop children's items and non-apparel accessories (the analog of the
                  MLE pipeline's PII / out-of-scope removal in silver)
  * hard validate - construct `schema.Product`, which enforces category, section and price;
                  a row that fails is dropped with a recorded reason

Returns (clean_products, report). The report feeds the gold manifest and the pipeline summary.
"""
import json

from pydantic import ValidationError

from app.config import SILVER_DIR

from . import schema
from .normalize import KIDS, _NOT_PRODUCT, size_system


def _in_vocab(values, vocab: set) -> tuple[list[str], list[str]]:
    """Lower-case, de-duplicate, keep only known tokens; return (kept, dropped)."""
    kept, dropped, seen = [], [], set()
    for v in values or []:
        t = str(v).strip().lower()
        if t in vocab:
            if t not in seen:
                seen.add(t)
                kept.append(t)
        elif t:
            dropped.append(t)
    return kept, dropped


def clean(raw: list[dict]) -> tuple[list[dict], dict]:
    colors_v = {c.lower() for c in schema.COLORS}
    styles_v = {s.lower() for s in schema.STYLES}
    occ_v = {o.lower() for o in schema.OCCASIONS}
    mats_v = set(schema.MATERIALS)

    clean: list[dict] = []
    seen_ids: set[str] = set()
    dropped: list[dict] = []
    stripped: list[dict] = []  # out-of-vocab tokens removed, for transparency

    for row in raw:
        pid = row.get("id", "?")
        # Scope-filter on the product name only (like the live normaliser, which tests the
        # title/type, not the free-text body: "...cushioned insole" must not drop a real shoe).
        name = str(row.get("name", "")).lower()
        if KIDS.search(name) or _NOT_PRODUCT.search(name):
            dropped.append({"id": pid, "reason": "out-of-scope (kids/accessory)"})
            continue
        if pid in seen_ids:
            dropped.append({"id": pid, "reason": "duplicate id"})
            continue

        colors, dc = _in_vocab(row.get("colors"), colors_v)
        materials, dm = _in_vocab(row.get("materials"), mats_v)
        styles, ds = _in_vocab(row.get("style_tags"), styles_v)
        occasions, do = _in_vocab(row.get("occasions"), occ_v)
        for field, toks in (("colors", dc), ("materials", dm), ("style_tags", ds), ("occasions", do)):
            stripped += [{"id": pid, "field": field, "token": t} for t in toks]

        stock = {str(s): q for s, q in (row.get("stock") or {}).items()}
        candidate = {
            **row,
            "colors": colors,
            "materials": materials,
            "style_tags": styles,
            "occasions": occasions,
            "stock": stock,
            "size_system": row.get("size_system") or size_system(row.get("category", ""), stock),
        }
        try:
            product = schema.Product(**candidate)
        except ValidationError as e:
            dropped.append({"id": pid, "reason": e.errors()[0]["msg"]})
            continue
        seen_ids.add(pid)
        clean.append(product.model_dump())

    report = {
        "in": len(raw),
        "kept": len(clean),
        "dropped": dropped,
        "vocab_tokens_stripped": len(stripped),
        "stripped": stripped,
    }
    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    (SILVER_DIR / "products.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")
    (SILVER_DIR / "_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return clean, report
