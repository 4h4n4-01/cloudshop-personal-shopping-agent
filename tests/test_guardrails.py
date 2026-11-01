"""Week-4 guardrails (W4-10..16): automatic, application-enforced checks that reject — distinct
from instructions (guidance) and approval (human). Input / output / tool guardrails."""
import os

os.environ["LLM_MODE"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "cardnetwork"

from app import guardrails  # noqa: E402


def test_input_guardrail_rejects_out_of_scope():
    assert guardrails.check_in_scope("book me a flight to Tokyo next week")        # travel
    assert guardrails.check_in_scope("order me a pizza")                           # food
    assert guardrails.check_in_scope("should I buy bitcoin?")                      # finance


def test_input_guardrail_allows_shopping_and_commands():
    assert guardrails.check_in_scope("a party dress, dark not black") is None      # fashion
    assert guardrails.check_in_scope("add the dress") is None                      # flow command
    assert guardrails.check_in_scope("that's it, checkout") is None                # flow command


def test_output_guardrail_blocks_failed_pick():
    assert guardrails.check_recommendation_addable("fail")      # must block
    assert guardrails.check_recommendation_addable("warn") is None
    assert guardrails.check_recommendation_addable("pass") is None


def test_availability_guardrail_named_colour_and_size():
    """When a named colour/size can't be met by any on-screen option, the guardrail reports it
    instead of letting a mismatched item be added; it stays quiet when the request is satisfiable."""
    cands = [{"colors": ["red", "beige"], "sizes": ["S", "M"]},
             {"colors": ["green", "beige"], "sizes": ["M", "L"]}]
    assert guardrails.check_add_satisfiable("olive", None, cands)["color_bad"]   # no olive anywhere
    assert guardrails.check_add_satisfiable(None, "44", cands)["size_bad"]       # no size 44
    assert guardrails.check_add_satisfiable("red", "m", cands) is None           # red + M both in cand 1
    v = guardrails.check_add_satisfiable("red", "l", cands)                      # red only in cand1, L only in cand2
    assert v and v["joint"] and not v["color_bad"] and not v["size_bad"]


def test_identity_guardrail_unmatched_brand():
    """A brand/product the shopper names that isn't on screen is reported, not silently substituted."""
    cands = ["Tiller & Twine Cotton Chino Trousers", "Oxford Row Cropped Wool Chino Trousers"]
    assert guardrails.unmatched_specifiers(["kestrel"], cands) == ["kestrel"]     # not a brand here
    assert guardrails.unmatched_specifiers(["cotton"], cands) == []               # appears in a name
    assert guardrails.unmatched_specifiers(["quay", "pleated"], ["Quay & Co Linen Shirt"]) == ["pleated"]


def test_tool_guardrail_payment_rules():
    known = {"pm_card_visa"}
    assert guardrails.check_payment(0, "pm_card_visa", known)                      # non-positive
    assert guardrails.check_payment(10_000, "pm_card_visa", known)                 # over hard max
    assert guardrails.check_payment(50, "pm_card_unknown", known)                  # unknown method
    assert guardrails.check_payment(50, "pm_card_visa", known) is None             # ok
