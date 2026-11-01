"""End-to-end flow through the HTTP API with offline mock agents."""
import os

os.environ["LLM_MODE"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app.tools import catalog  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _session():
    catalog.reset_catalog()
    return client.post("/api/session").json()["session_id"]


def test_full_purchase_flow():
    sid = _session()
    s = client.post(f"/api/session/{sid}/intake",
                    json={"message": "A summer wedding guest dress for women, size M, nothing polyester, under $150"}).json()
    b = s["brief"]
    assert b["items_wanted"] == ["dress"] and b["size_top"] == "M" and b["budget_total"] == 150
    assert b["materials_avoided"] == ["polyester"] and b["provenance"]["size_top"] == "stated"

    s = client.post(f"/api/session/{sid}/recommend", json={}).json()
    recs = s["recommendations"]
    assert recs, "stylist returned no picks"
    for r in recs:
        assert "polyester" not in r["product"]["materials"]
        assert r["product"]["price"] <= 150
        assert r["verdict"] in ("pass", "warn")

    pick = recs[0]
    s = client.post(f"/api/session/{sid}/cart", json={"product_id": pick["product"]["id"], "size": pick["size"]}).json()
    s = client.post(f"/api/session/{sid}/checkout/plan", json={"spending_cap": 150, "payment_method": "pm_card_visa"}).json()
    plan = s["plan"]
    assert plan["status"] == "awaiting_approval" and not s["orders"]

    s = client.post(f"/api/session/{sid}/checkout/approve", json={"plan_id": plan["plan_id"]}).json()
    assert s["orders"] and s["orders"][0]["total"] == plan["total"] and s["cart"] == []


def test_clarification_and_edit():
    sid = _session()
    s = client.post(f"/api/session/{sid}/intake", json={"message": "Something cozy for winter"}).json()
    fields = {c["field"] for c in s["clarifications"]}
    assert "size_top" in fields
    s = client.post(f"/api/session/{sid}/clarify", json={"field": "size_top", "value": "L"}).json()
    assert s["brief"]["size_top"] == "L"
    assert "size_top" not in {c["field"] for c in s["clarifications"]}

    edited = s["brief"] | {"budget_total": 90}
    s = client.put(f"/api/session/{sid}/brief", json=edited).json()
    assert s["brief"]["provenance"]["budget_total"] == "edited"


def test_cart_change_invalidates_approval():
    sid = _session()
    client.post(f"/api/session/{sid}/cart", json={"product_id": "W-TP-002", "size": "M"})
    plan = client.post(f"/api/session/{sid}/checkout/plan", json={}).json()["plan"]
    client.post(f"/api/session/{sid}/cart", json={"product_id": "M-TP-001", "size": "L"})
    r = client.post(f"/api/session/{sid}/checkout/approve", json={"plan_id": plan["plan_id"]})
    assert r.status_code == 400
    assert client.get(f"/api/session/{sid}").json()["orders"] == []


def test_declined_card_charges_nothing():
    sid = _session()
    before = catalog.get("W-TP-002")["stock"]["M"]
    client.post(f"/api/session/{sid}/cart", json={"product_id": "W-TP-002", "size": "M"})
    plan = client.post(f"/api/session/{sid}/checkout/plan", json={"payment_method": "pm_card_declined"}).json()["plan"]
    s = client.post(f"/api/session/{sid}/checkout/approve", json={"plan_id": plan["plan_id"]}).json()
    assert s["last_payment_error"] == "card_declined" and not s["orders"]
    assert catalog.get("W-TP-002")["stock"]["M"] == before


def test_spending_cap_needs_override():
    sid = _session()
    client.post(f"/api/session/{sid}/cart", json={"product_id": "W-CT-001", "size": "M"})
    plan = client.post(f"/api/session/{sid}/checkout/plan", json={"spending_cap": 100}).json()["plan"]
    assert plan["requires_cap_override"]
    assert client.post(f"/api/session/{sid}/checkout/approve", json={"plan_id": plan["plan_id"]}).status_code == 400
    s = client.post(f"/api/session/{sid}/checkout/approve", json={"plan_id": plan["plan_id"], "cap_override": True}).json()
    assert s["orders"]
