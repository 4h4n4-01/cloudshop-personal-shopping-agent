"""Agent memory (Week 4): personalisation from a returning customer's history."""
import os

os.environ["LLM_MODE"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app import memory  # noqa: E402
from app.tools import catalog  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _session():
    catalog.reset_catalog()
    return client.post("/api/session").json()["session_id"]


def test_profile_derived_from_history():
    prof = memory.profile("cust_ava")
    assert prof and prof["name"] == "Ava"
    # Ava's stated likes and history both feed the derived profile
    assert "navy" in prof["colors_liked"] or "cream" in prof["colors_liked"]
    assert "polyester" in prof["materials_avoided"]   # she rejects polyester
    assert prof["evidence"]["kept"] and prof["evidence"]["rejected"]


def test_sign_in_prefills_brief_and_personalises():
    sid = _session()
    s = client.post(f"/api/session/{sid}/customer", json={"customer_id": "cust_ava"}).json()
    assert s["customer"]["name"] == "Ava" and s["memory"]
    b = s["brief"]
    assert b["section"] == "women" and b["size_top"] == "M"
    assert b["provenance"]["size_top"] == "remembered"
    assert "polyester" in b["materials_avoided"]

    # a new request merges onto the remembered brief; personalised rationale cites the history
    client.post(f"/api/session/{sid}/intake", json={"message": "a dress for a summer wedding"})
    recs = client.post(f"/api/session/{sid}/recommend", json={}).json()["recommendations"]
    assert recs
    assert any("history" in r["rationale"].lower() or "remembered" in r["matched_preferences"] for r in recs)
    for r in recs:
        assert "polyester" not in r["product"]["materials"]   # memory filters her avoided material


def test_adding_to_cart_updates_memory():
    sid = _session()
    client.post(f"/api/session/{sid}/customer", json={"customer_id": "cust_leo"})
    before = len(client.get(f"/api/session/{sid}").json()["memory"]["evidence"]["kept"])
    client.post(f"/api/session/{sid}/intake", json={"message": "a black hoodie, size M"})
    recs = client.post(f"/api/session/{sid}/recommend", json={}).json()["recommendations"]
    pick = recs[0]
    s = client.post(f"/api/session/{sid}/cart", json={"product_id": pick["product"]["id"], "size": pick["size"]}).json()
    assert len(s["memory"]["evidence"]["kept"]) == before + 1


def test_sign_out_clears_personalisation():
    sid = _session()
    client.post(f"/api/session/{sid}/customer", json={"customer_id": "cust_ava"})
    s = client.post(f"/api/session/{sid}/customer", json={"customer_id": None}).json()
    assert s["customer"] is None and s["memory"] is None


def test_only_featured_customers_offered():
    offered = client.get("/api/customers").json()
    assert len(offered) == 8 and all(c["id"].startswith("cust_") for c in offered)


def test_collaborative_filtering_finds_lookalikes():
    nbrs = memory.neighbors("cust_ava", k=10)
    assert nbrs and nbrs[0][0] > 0                       # has similar shoppers, positive similarity
    # neighbours are more similar than a random streetwear shopper is to elegant Ava
    sims = [s for s, _ in nbrs]
    assert sims == sorted(sims, reverse=True)            # ranked by similarity
    suggestions = memory.collaborative_suggestions("cust_ava", k=25)
    assert suggestions                                   # items similar shoppers chose
    assert all(v >= 1 for v in suggestions.values())


def test_similar_shoppers_surface_in_picks():
    sid = _session()
    client.post(f"/api/session/{sid}/customer", json={"customer_id": "cust_sofia"})
    client.post(f"/api/session/{sid}/intake", json={"message": "a dress, size S"})
    recs = client.post(f"/api/session/{sid}/recommend", json={}).json()["recommendations"]
    assert recs
    assert any("similar shoppers" in r["matched_preferences"] for r in recs)
