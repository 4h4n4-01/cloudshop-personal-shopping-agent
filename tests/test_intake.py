"""IDEA 1 (Week 1): the Intake Agent's offline NLP across real human prompt styles.

Each test names the prompt style from app/prompts.py and the envisioning gap it exercises, so the
suite doubles as evidence that the intent classifier handles how non-experts actually phrase things
(Zamfirescu-Pereira et al. 2023; Subramonyam et al. 2024).
"""
import os

os.environ["LLM_MODE"] = "mock"

from app.agents import intake  # noqa: E402
from app.models import ShoppingBrief  # noqa: E402
from app import prompts  # noqa: E402


def _brief(msg: str, current=None) -> ShoppingBrief:
    out = intake._mock_parse(msg, current)
    return intake._to_brief(out, current)


def test_social_lens_is_stripped():
    """Politeness / CASA must not change the extraction (W1-17/18)."""
    b = _brief("Hi there! Could you please help me find a black dress? Thank you so much 😊")
    assert b.items_wanted == ["dress"]
    assert "black" in b.colors_liked


def test_vague_adjective_maps_to_style():
    """Instruction gap: 'classy'/'comfy' map onto the controlled style vocab (W1-39)."""
    assert "elegant" in _brief("something classy for dinner").style
    assert "cozy" in _brief("a comfy sweater").style
    assert "minimalist" in _brief("a simple clean white tee").style


def test_negation_routes_to_avoided():
    """'nothing black', 'no polyester', 'not into' -> avoided, never liked."""
    b = _brief("a shirt, nothing black and no polyester please")
    assert "black" in b.colors_avoided and "black" not in b.colors_liked
    assert "polyester" in b.materials_avoided


def test_negation_with_verb_and_multiple_colours():
    """Verb-separated, multi-value negation: 'dont wanna wear black and white' -> BOTH avoided,
    neither liked. Also 'meeting with client' -> office. (Regression: the old regex grabbed only
    the word after the trigger, so the colours leaked into colors_liked.)"""
    b = _brief("I have a meeting with client and I dont wanna wear black and white")
    assert set(b.colors_avoided) == {"black", "white"}
    assert b.colors_liked == []
    assert b.occasion == "office"


def test_negation_does_not_swallow_later_liked_colour():
    """A positive clause after the negation stays liked: 'no black but I love navy'."""
    b = _brief("a dress, no black but I love navy")
    assert "black" in b.colors_avoided
    assert "navy" in b.colors_liked and "navy" not in b.colors_avoided


def test_shorthand_and_typos_normalised():
    """Shorthand style: 'blk dress sz M summr' still parses."""
    b = _brief("blk dress sz M summr")
    assert b.items_wanted == ["dress"] and b.size_top == "M"
    assert "black" in b.colors_liked and b.occasion == "summer"


def test_multi_item_to_match():
    """Multi-item compound intent keeps both pieces and records the match relationship."""
    b = _brief("a dress and heels to match for a party")
    assert "dress" in b.items_wanted and "heels" in b.items_wanted
    assert "match" in b.notes.lower()


def test_occasion_infers_style_and_is_surfaced():
    """Occasion-first: style inferred from the event AND flagged as inferred (not silently added)."""
    out = intake._mock_parse("something for a wedding", None)
    assert out.occasion == "wedding-guest"
    assert "elegant" in out.style and "style" in out.inferred_fields


def test_clarifications_carry_gap_tags():
    """Every clarification names the envisioning gap it crosses (W1-37)."""
    out = intake._mock_parse("I need jeans", None)
    gaps = {c.gap for c in out.clarifications}
    assert gaps <= {"capability", "instruction", "intentionality"} and None not in gaps


def test_llm_backfill_recovers_dropped_fields():
    """The LLM-path safety-net: fill fields the model omitted (size, a 2nd avoided colour, items)
    from the deterministic parser, without overriding what the model did set."""
    from app.models import IntakeLLMOutput
    # Simulate a capable model that caught the occasion + one colour but dropped size, items, and
    # the second avoided colour that the shopper stated plainly.
    llm_out = IntakeLLMOutput(
        items_wanted=[], section=None, occasion="office", style=[], colors_liked=[],
        colors_avoided=["black"], materials_avoided=[], size_top=None, size_bottom=None,
        size_shoe=None, budget_total=None, budget_per_item=None, notes="",
        inferred_fields=[], clarifications=[], summary="x")
    out = intake._backfill(llm_out, "a loose dress for the office, size XL, no black or white", None)
    assert "dress" in out.items_wanted                       # items unioned in
    assert out.size_top == "XL"                              # size backfilled
    assert set(out.colors_avoided) == {"black", "white"}     # kept black, added white
    assert out.occasion == "office"                          # LLM value untouched
    assert "non-tight" in out.notes                          # loose-fit cue appended


def test_starter_suggestions_deterministic_yet_dynamic():
    """Same seed -> same chips (reproducible); different seed -> a different set."""
    assert prompts.starter_suggestions(seed=7, n=6) == prompts.starter_suggestions(seed=7, n=6)
    assert prompts.starter_suggestions(seed=7, n=6) != prompts.starter_suggestions(seed=8, n=6)
