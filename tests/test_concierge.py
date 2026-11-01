"""The conversational concierge: one chat thread driving the whole pipeline (intent -> outfit ->
curate the cart by chat -> 'anything else?' -> checkout). Mirrors the target user journey."""
import os

os.environ["LLM_MODE"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "cardnetwork"

from fastapi.testclient import TestClient  # noqa: E402

from app.tools import catalog  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def _session():
    catalog.reset_catalog()
    return client.post("/api/session").json()["session_id"]


def _chat(sid, msg):
    return client.post(f"/api/session/{sid}/chat", json={"message": msg}).json()


def _cats(state, key):
    if key == "cart":
        return [catalog.get(x["product_id"])["category"] for x in state["cart"]]
    return sorted({r["product"]["category"] for r in state["recommendations"]})


def test_full_conversational_journey():
    sid = _session()
    # 1. messy, emotional first request -> outfit suggested
    s = _chat(sid, "party tomorrow, clubby but comfy, dark not black, and I hate sequins")
    assert s["recommendations"] and s["view_hint"] == "discover"
    assert "non-tight fit" in s["brief"]["notes"] and "sequins" in s["brief"]["notes"]

    # 2. curate by chat: take the dress + bag, skip the shoes
    s = _chat(sid, "I like the dress but I don't need shoes, I'll just go with the bag and the dress")
    cart = _cats(s, "cart")
    assert "dress" in cart and "bag" in cart and "shoes" not in cart
    assert s["view_hint"] == "checkout"

    # 3. "anything else?" -> a new request in the same thread
    s = _chat(sid, "maybe another top, I have a date next Tuesday")
    assert "blouse" in _cats(s, "discover") or s["recommendations"]  # new options surfaced

    # 4. checkout -> the hardened secure flow starts
    s = _chat(sid, "that's it, please check out")
    assert s["secure"] and s["secure"]["stage"] == "cart_review"


def test_skip_does_not_add():
    sid = _session()
    _chat(sid, "I need a party outfit")
    s = _chat(sid, "just the dress, no shoes and no bag")
    cart = _cats(s, "cart")
    assert "dress" in cart and "shoes" not in cart and "bag" not in cart


def test_no_match_explains_the_blocking_constraint():
    """An empty shortlist must say WHY (e.g. size 26 isn't stocked for men's trousers) and what IS
    available — not a vague 'loosen the budget'."""
    sid = _session()
    s = _chat(sid, "black trousers size 26 for men")
    assert not s["recommendations"]
    reply = s["messages"][-1]["text"].lower()
    assert "26" in reply and "size" in reply and ("available" in reply or "28" in reply)


def test_checkout_chat_switches_card_and_never_autopays():
    """In secure checkout, the chat can switch the card for real, but can NEVER complete payment
    (that needs the on-screen gates). The reply must not claim an order was placed."""
    sid = _session()
    _chat(sid, "a dress for a party")
    _chat(sid, "add the dress")
    s = _chat(sid, "let's check out")
    assert s["secure"] and s["secure"]["stage"] == "cart_review"
    s = _chat(sid, "can you use my mastercard instead")
    assert s["secure"]["plan"]["payment_method"] == "pm_card_mc"       # actually switched
    assert "mastercard" in s["messages"][-1]["text"].lower()
    s = _chat(sid, "ok proceed and pay now")
    assert not s["orders"]                                             # chat never auto-pays
    assert "pay" in s["messages"][-1]["text"].lower()


def test_checkout_on_empty_cart_is_graceful():
    sid = _session()
    s = _chat(sid, "let's check out")
    assert s["secure"] is None and "empty" in s["messages"][-1]["text"].lower()


def test_clause_isolates_each_item_descriptors():
    """A multi-item add must not let one item's words bleed onto the other: 'quay and co shirt as
    well as the oversize cotton chino trousers in olive size 36' — 'olive' belongs to the trousers
    only, and the brand 'quay and co' stays intact."""
    from app.orchestrator import _clause_for
    text = " can u give me the quay and co shirt as well as the oversize cotton chino trousers in olive size 36 "
    cs, ct = _clause_for(text, "shirt"), _clause_for(text, "trousers")
    assert "quay" in cs and "olive" not in cs
    assert "olive" in ct and "quay" not in ct


def test_add_reply_names_the_real_item():
    """The add confirmation must describe the item ACTUALLY carted, not echo the request
    (guards against 'Perfect, I added the Quay & Co shirt' when a different shirt went in)."""
    sid = _session()
    _chat(sid, "I need a dress for a party")
    s = _chat(sid, "add the dress")
    assert s["cart"], "a dress should be in the cart"
    real_name = catalog.get(s["cart"][0]["product_id"])["name"]
    reply = s["messages"][-1]["text"]
    assert reply.startswith("Added") and real_name in reply


def test_remove_from_cart_by_name():
    sid = _session()
    _chat(sid, "I need a dress and a bag for a party")
    _chat(sid, "add the dress and the bag")
    s = _chat(sid, "actually remove the bag")
    assert "bag" not in _cats(s, "cart")


def test_transcript_is_recorded():
    sid = _session()
    s = _chat(sid, "hello")
    roles = [m["role"] for m in s["messages"]]
    assert roles[0] == "user" and roles[-1] == "assistant"


def test_weak_model_no_action_falls_back_to_router():
    """If a small LLM returns a chatty reply but no action, the deterministic router supplies one
    so the pipeline still advances — and the LLM's natural reply is kept."""
    from app.agents import concierge
    from app.models import ConciergeTurn
    chatty = ConciergeTurn(reply="Would you like to explore some options?", actions=[])
    ctx = {"has_recs": False, "cart_categories": [], "rec_categories": []}
    out = concierge._ensure_actionable(chatty, "find me a loose teal dress for a conference", ctx)
    assert any(a.kind == "shop" for a in out.actions)
    assert out.reply == "Would you like to explore some options?"


def test_refine_phrasings_rerun_the_stylist():
    """'these are wrong, give me the correct ones' and friends must route to a refine, not stall."""
    from app.agents import concierge
    ctx = {"has_recs": True, "cart_categories": [], "rec_categories": ["dress"]}
    for msg in ["these are wrong, give me the correct ones",
                "no these aren't right, try again",
                "show me different options",
                "that's not what I asked for",
                "wrong, show me others"]:
        turn = concierge._mock_route(msg, ctx)
        assert any(a.kind == "refine" for a in turn.actions), f"did not refine: {msg!r}"


def test_real_llm_action_is_preserved():
    """A capable model's explicit action is never overridden by the fallback."""
    from app.agents import concierge
    from app.models import ConciergeTurn, ConciergeAction
    t = ConciergeTurn(reply="Let's check out.", actions=[ConciergeAction(kind="checkout")])
    out = concierge._ensure_actionable(t, "pay now", {"cart_categories": ["dress"]})
    assert [a.kind for a in out.actions] == ["checkout"]
