"""Shoes (EU/UK/US size conversion) and bags (one size), plus the kids filter."""
import os

os.environ["LLM_MODE"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app.tools import catalog, sizes  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SHOE_CATEGORIES  # noqa: E402

client = TestClient(app)


def test_size_conversion():
    assert sizes.to_eu("EU 38") == sizes.to_eu("38") == sizes.to_eu("UK 5") == 38
    assert sizes.to_eu("US 7", "women") == 38 and sizes.to_eu("US 9", "men") == 41
    assert sizes.to_eu("8") is None and sizes.to_eu("M") is None  # ambiguous / not a shoe size
    assert sizes.to_eu("M10/W12", "women") == 42 and sizes.label("M10/W12") == "M10/W12"  # men's scale label
    assert sizes.label("40") == "EU 40" and sizes.label("us 8.5") == "US 8.5" and sizes.label("One Size") == "One Size"
    stock = {"EU 37": 1, "EU 38": 0, "EU 38.5": 2}
    assert sizes.pick(stock, "UK 5", None, "women") == "EU 38"  # exact size wins, even sold out
    assert sizes.pick({"EU 37": 1, "EU 38.5": 2}, "UK 5", None, "women") == "EU 38.5"  # else half a size
    assert sizes.pick(stock, "EU 41", None, "women") is None
    note, exact = sizes.explain("UK 5", "EU 38", None, "women")
    assert note == "You said UK 5 ≈ EU 38; this store lists EU 38" and exact


def _session():
    catalog.reset_catalog()
    return client.post("/api/session").json()["session_id"]


def test_heels_and_bag_flow():
    sid = _session()
    s = client.post(f"/api/session/{sid}/intake",
                    json={"message": "Black heels for a wedding, UK 5, under $120, and a small bag to match"}).json()
    b = s["brief"]
    assert b["items_wanted"] == ["heels", "bag"] and b["size_shoe"] == "UK 5"
    assert b["size_top"] is None and b["size_bottom"] is None
    assert "size_top" not in [c["field"] for c in s["clarifications"]]  # no clothing size needed

    recs = client.post(f"/api/session/{sid}/recommend", json={}).json()["recommendations"]
    by_cat = {}
    for r in recs:
        by_cat.setdefault(r["product"]["category"], []).append(r)
    assert set(by_cat) == {"heels", "bag"}
    heel = by_cat["heels"][0]
    assert heel["product"]["id"] == "W-SH-001" and heel["size"] == "EU 38"  # the cream pair is sold out in 38
    size_check = next(c for c in heel["checks"] if c["name"] == "Size in stock")
    assert size_check["status"] == "pass" and size_check["evidence"].startswith("You said UK 5 ≈ EU 38")
    assert all(r["size"] == "One Size" for r in by_cat["bag"])

    s = client.post(f"/api/session/{sid}/cart", json={"product_id": "W-SH-001", "size": "EU 38"}).json()
    assert s["cart"][0]["size"] == "EU 38"


def test_generic_shoes_and_shoe_size_question():
    sid = _session()
    s = client.post(f"/api/session/{sid}/intake", json={"message": "comfy shoes for travel"}).json()
    assert s["brief"]["items_wanted"] == ["shoes"]
    assert "size_shoe" in [c["field"] for c in s["clarifications"]]
    client.post(f"/api/session/{sid}/clarify", json={"field": "size_shoe", "value": "US 9"})
    recs = client.post(f"/api/session/{sid}/recommend", json={}).json()["recommendations"]
    assert recs and all(r["product"]["category"] in SHOE_CATEGORIES for r in recs)
    sneaker = next(r for r in recs if r["product"]["id"] == "U-SH-001")
    assert sneaker["size"] == "US 9"  # US 9 (men's scale) = EU 41 = the store's "US 9"


def test_waist_not_confused_with_shoe_size():
    sid = _session()
    b = client.post(f"/api/session/{sid}/intake", json={"message": "loafers size 42 for men"}).json()["brief"]
    assert b["size_shoe"] == "42" and b["size_bottom"] is None
