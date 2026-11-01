"""Prompt library for the Intake Agent.

Instead of giving the shopper a blank text box, we suggest the useful dimensions and concrete
language for describing what they want. This lowers the gap between what someone wants and knowing
how to ask for it. Every starter prompt and refinement dimension is tagged with the kind of gap it
helps the shopper cross (capability / instruction / intentionality), so the suggestion chips are
grounded in how people actually express intent rather than being decorative.

PROMPT_STYLES below catalogues the recurring ways non-expert shoppers phrase requests:
  - terse keywords with almost no context,
  - polite, conversational phrasing aimed at the AI as if it were a person,
  - vague adjectives and instructions that aren't concrete properties,
  - intent that is fluid, under-specified, and spread across several turns.
The Intake Agent recognises and normalises each style (see app/agents/intake.py).
"""
import random

# The three envisioning gaps. Used as a tag on every suggestion and clarification.
CAPABILITY = "capability"        # "what is feasible / what should I even specify?"
INSTRUCTION = "instruction"      # "how do I phrase this so it does what I mean?"
INTENTIONALITY = "intentionality"  # "what would a *good* result even look like?"

GAP_LABEL = {
    CAPABILITY: "Capability gap, surfaces what you can specify",
    INSTRUCTION: "Instruction gap, turns a vague word into a concrete property",
    INTENTIONALITY: "Intentionality gap, helps you decide what 'good' looks like",
}


# --------------------------------------------------------------------------- #
# How real shoppers phrase requests (the "research" layer the Intake Agent handles).
# Each entry: the cue the user gives, the underlying concept, and how Intake normalises it.
# --------------------------------------------------------------------------- #
PROMPT_STYLES = [
    {
        "id": "keyword",
        "label": "Terse keyword",
        "gap": CAPABILITY,
        "cue": "one or two words, almost no context",
        "example": "black jeans",
        "concept": "Capability gap, the shopper doesn't yet know which dimensions matter.",
        "handling": "Extract the item, then suggest the dimensions worth adding (fit, occasion, budget).",
    },
    {
        "id": "social",
        "label": "Polite / social-lens",
        "gap": INSTRUCTION,
        "cue": "greetings, 'please', 'could you', emoji, talks to the AI like a person",
        "example": "Hi! Could you please help me find a nice dress for a dinner? Thank you 😊",
        "concept": "People apply human conversational norms to the AI, as if talking to a person.",
        "handling": "Strip the social filler, keep the actual request; never let politeness change the extraction.",
    },
    {
        "id": "occasion",
        "label": "Occasion-first",
        "gap": INTENTIONALITY,
        "cue": "names an event or outcome but not the garment or its properties",
        "example": "something for a summer wedding",
        "concept": "Intentionality gap, outcome stated, criteria for 'good' undeveloped.",
        "handling": "Infer likely category/style from the occasion (and say so), then ask for the deciding criteria.",
    },
    {
        "id": "adjective",
        "label": "Vague adjective",
        "gap": INSTRUCTION,
        "cue": "qualitative words with many readings: elegant, cool, smart, comfy",
        "example": "make it elegant but not too flashy",
        "concept": "Instruction gap, adjectives aren't concrete output properties.",
        "handling": "Map adjectives onto the controlled style vocabulary and offer concrete options.",
    },
    {
        "id": "negation",
        "label": "Constraint / negation",
        "gap": INSTRUCTION,
        "cue": "says what to avoid: 'no X', 'nothing Y', 'not into Z', 'anything but'",
        "example": "under $80, no polyester, nothing black",
        "concept": "Clear intent expressed as exclusions, easy to mis-parse as *wanted*.",
        "handling": "Route negated colours/materials into colors_avoided / materials_avoided, not liked.",
    },
    {
        "id": "multi",
        "label": "Multi-item",
        "gap": CAPABILITY,
        "cue": "asks for more than one piece, often 'to match'",
        "example": "a dress and heels to match",
        "concept": "Several wants packed into a single turn, to be pulled apart.",
        "handling": "Split into multiple items_wanted and keep the matching relationship in notes.",
    },
    {
        "id": "comparative",
        "label": "Comparative / 'like last time'",
        "gap": INTENTIONALITY,
        "cue": "references past purchases or personal taste",
        "example": "something like the jacket I bought last time",
        "concept": "Intent that leans on past choices, so it needs the shopper's history.",
        "handling": "Defer to Agent Memory (Idea 4) to ground 'my style' in prior kept items.",
    },
    {
        "id": "shorthand",
        "label": "Shorthand / typo",
        "gap": CAPABILITY,
        "cue": "abbreviations and misspellings: 'blk dress sz M summr'",
        "example": "blk dress sz M summr",
        "concept": "Low-effort entry, fluent language doesn't guarantee a clean spec.",
        "handling": "Normalise common abbreviations/typos before extraction.",
    },
]


# --------------------------------------------------------------------------- #
# Starter prompts, the clickable "Suggest prompting ideas" chips.
# Realistic phrasings, each tagged with the style it models and the gap it crosses.
# Kept deliberately varied so the shopper sees the *range* of what they can say.
# --------------------------------------------------------------------------- #
STARTER_PROMPTS = [
    {"text": "A linen shirt for a beach wedding, under $90", "style": "occasion", "gap": INTENTIONALITY},
    {"text": "Comfy everyday jeans that aren't skinny, size 32", "style": "negation", "gap": INSTRUCTION},
    {"text": "Something elegant for a dinner date, nothing too flashy", "style": "adjective", "gap": INSTRUCTION},
    {"text": "A warm winter coat in a neutral colour, under $200", "style": "occasion", "gap": INTENTIONALITY},
    {"text": "Office-appropriate blazer, no polyester", "style": "negation", "gap": INSTRUCTION},
    {"text": "A dress and heels to match for a summer party", "style": "multi", "gap": CAPABILITY},
    {"text": "Minimalist white sneakers for everyday wear", "style": "keyword", "gap": CAPABILITY},
    {"text": "Cozy knitwear for cold weather, size M", "style": "adjective", "gap": INSTRUCTION},
    {"text": "Smart-casual outfit for a first day at work", "style": "occasion", "gap": INTENTIONALITY},
    {"text": "A crossbody bag that goes with everything, under $120", "style": "keyword", "gap": CAPABILITY},
    {"text": "Breathable gym shorts, nothing tight", "style": "negation", "gap": INSTRUCTION},
    {"text": "A vintage-style denim jacket for men", "style": "adjective", "gap": INSTRUCTION},
]


# --------------------------------------------------------------------------- #
# Refinement dimensions, the concrete knobs that convert a vague brief into a precise one.
# This is the instruction-gap scaffold: show the dimension AND concrete language.
# The Intake Agent offers the subset that would actually change which products are shown.
# --------------------------------------------------------------------------- #
REFINEMENT_DIMENSIONS = [
    {"field": "occasion", "label": "Occasion", "gap": INTENTIONALITY,
     "question": "Where will you wear it?",
     "options": ["everyday", "office", "a wedding", "a date", "the beach", "the gym"]},
    {"field": "style", "label": "Vibe", "gap": INSTRUCTION,
     "question": "What feel are you going for?",
     "options": ["minimalist", "classic", "elegant", "casual", "streetwear", "cozy"]},
    {"field": "formality", "label": "Formality", "gap": INSTRUCTION,
     "question": "How dressed-up?",
     "options": ["very casual", "smart-casual", "formal"]},
    {"field": "colors_liked", "label": "Palette", "gap": INSTRUCTION,
     "question": "Any colours you lean toward?",
     "options": ["neutrals", "navy & blue", "earth tones", "black & white", "a pop of colour"]},
    {"field": "budget_total", "label": "Budget", "gap": INTENTIONALITY,
     "question": "What's your budget?",
     "options": ["Under $50", "Under $100", "Under $200", "Under $400"]},
    {"field": "materials_avoided", "label": "Fabric to avoid", "gap": INSTRUCTION,
     "question": "Anything you'd rather not wear?",
     "options": ["no polyester", "no wool", "no leather"]},
]


def starter_suggestions(seed: int | None = None, n: int = 6) -> list[dict]:
    """A deterministic-yet-dynamic sample of starter prompts (the user asked for exactly this):
    same seed -> same chips (reproducible for the demo/eval), new seed -> a fresh, varied set.
    Balances across envisioning gaps so the shopper always sees the full range of prompt styles."""
    rng = random.Random(seed)
    by_gap: dict[str, list[dict]] = {}
    for p in STARTER_PROMPTS:
        by_gap.setdefault(p["gap"], []).append(p)
    picks: list[dict] = []
    gaps = list(by_gap)
    rng.shuffle(gaps)
    # round-robin across the three gaps so suggestions never collapse onto one style
    pools = {g: rng.sample(by_gap[g], len(by_gap[g])) for g in gaps}
    while len(picks) < min(n, len(STARTER_PROMPTS)):
        for g in gaps:
            if pools[g]:
                picks.append(pools[g].pop())
                if len(picks) >= min(n, len(STARTER_PROMPTS)):
                    break
    return picks
