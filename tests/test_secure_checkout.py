"""IDEA 3 (hardened): the multi-gate, human-in-the-loop payment flow over the Visa/Mastercard-rails
CardNetwork gateway. Each test asserts one guardrail, framed in Week-2 terms (appropriate reliance).
"""
import os

os.environ["LLM_MODE"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "cardnetwork"

from fastapi.testclient import TestClient  # noqa: E402

from app.tools import catalog  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
CARD = "pm_card_visa"


def _session_with_item(pid="W-TP-002", size="M"):
    catalog.reset_catalog()
    sid = client.post("/api/session").json()["session_id"]
    client.post(f"/api/session/{sid}/cart", json={"product_id": pid, "size": size})
    return sid


def _secure(sid):
    return client.get(f"/api/session/{sid}").json()["secure"]


def _begin(sid, cap=None, card=CARD):
    return client.post(f"/api/session/{sid}/secure/begin",
                       json={"spending_cap": cap, "payment_method": card}).json()


def _walk_to_otp(sid, cap=None, card=CARD):
    _begin(sid, cap, card)
    client.post(f"/api/session/{sid}/secure/confirm-cart")
    client.post(f"/api/session/{sid}/secure/confirm-card", json={"payment_method": card})
    client.post(f"/api/session/{sid}/secure/confirm-review", json={"cap_override": False})
    return _secure(sid)


def test_happy_path_captures_once_after_every_gate():
    sid = _session_with_item()
    s = _walk_to_otp(sid)
    assert s["stage"] == "otp" and not client.get(f"/api/session/{sid}").json()["orders"]
    otp = s["twofa"]["demo_otp"]
    client.post(f"/api/session/{sid}/secure/otp", json={"code": otp})
    assert _secure(sid)["stage"] == "authorized"
    out = client.post(f"/api/session/{sid}/secure/finalize").json()
    assert out["secure"]["stage"] == "captured"
    assert len(out["orders"]) == 1 and out["cart"] == []  # cart cleared, order placed


def test_no_charge_before_all_gates():
    sid = _session_with_item()
    _begin(sid)
    for step in ["confirm-cart", "confirm-card", "confirm-review"]:
        body = {"payment_method": CARD} if step == "confirm-card" else {"cap_override": False} if step == "confirm-review" else None
        client.post(f"/api/session/{sid}/secure/{step}", json=body)
        assert client.get(f"/api/session/{sid}").json()["orders"] == []  # nothing yet


def test_cannot_skip_to_finalize():
    sid = _session_with_item()
    _begin(sid)  # still at cart_review
    r = client.post(f"/api/session/{sid}/secure/finalize")
    assert r.status_code == 400 and not client.get(f"/api/session/{sid}").json()["orders"]


def test_otp_is_required_agent_cannot_bypass():
    sid = _session_with_item()
    _walk_to_otp(sid)
    # Cannot finalize while 2FA is pending (the OTP step is human-only).
    assert client.post(f"/api/session/{sid}/secure/finalize").status_code == 400
    assert not client.get(f"/api/session/{sid}").json()["orders"]


def test_wrong_otp_three_times_voids_nothing_charged():
    sid = _session_with_item()
    _walk_to_otp(sid)
    for _ in range(3):
        client.post(f"/api/session/{sid}/secure/otp", json={"code": "000000"})  # deliberately wrong
    s = _secure(sid)
    assert s["stage"] == "rejected" and s["last_error"] == "otp_attempts_exceeded"
    assert client.get(f"/api/session/{sid}").json()["orders"] == []


def test_cart_change_mid_flow_resets_binding():
    sid = _session_with_item()
    _begin(sid)
    client.post(f"/api/session/{sid}/secure/confirm-cart")
    # Tamper with the cart after confirming it.
    client.post(f"/api/session/{sid}/cart", json={"product_id": "M-TP-001", "size": "L"})
    r = client.post(f"/api/session/{sid}/secure/confirm-card", json={"payment_method": CARD})
    assert r.status_code == 400
    assert _secure(sid)["stage"] == "cart_review"  # bounced back to re-confirm


def test_spending_cap_requires_override():
    sid = _session_with_item(pid="W-CT-001", size="M")  # a pricier coat
    _begin(sid, cap=1.0)  # cap below the price
    client.post(f"/api/session/{sid}/secure/confirm-cart")
    client.post(f"/api/session/{sid}/secure/confirm-card", json={"payment_method": CARD})
    blocked = client.post(f"/api/session/{sid}/secure/confirm-review", json={"cap_override": False})
    assert blocked.status_code == 400  # cannot pass review without the override
    ok = client.post(f"/api/session/{sid}/secure/confirm-review", json={"cap_override": True})
    assert ok.json()["secure"]["stage"] == "otp"  # override lets it proceed to 2FA


def test_declined_card_rejected_at_authorization():
    sid = _session_with_item()
    _begin(sid, card="pm_card_declined")
    client.post(f"/api/session/{sid}/secure/confirm-cart")
    client.post(f"/api/session/{sid}/secure/confirm-card", json={"payment_method": "pm_card_declined"})
    client.post(f"/api/session/{sid}/secure/confirm-review", json={"cap_override": False})
    s = _secure(sid)
    assert s["stage"] == "rejected" and s["last_error"] == "card_declined"
    assert client.get(f"/api/session/{sid}").json()["orders"] == []


def test_no_replay_after_capture():
    sid = _session_with_item()
    s = _walk_to_otp(sid)
    client.post(f"/api/session/{sid}/secure/otp", json={"code": s["twofa"]["demo_otp"]})
    client.post(f"/api/session/{sid}/secure/finalize")  # captured
    again = client.post(f"/api/session/{sid}/secure/finalize")  # replay attempt
    assert again.status_code == 400
    assert len(client.get(f"/api/session/{sid}").json()["orders"]) == 1  # not double-charged
