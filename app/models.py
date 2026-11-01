"""Shared data models passed between agents and the GUI."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

CATEGORIES = [
    "dress", "t-shirt", "shirt", "blouse", "knitwear", "hoodie", "jacket", "blazer",
    "coat", "jeans", "trousers", "skirt", "shorts",
    "shoes", "sneakers", "heels", "flats", "sandals", "boots", "loafers", "bag",
]
BOTTOM_CATEGORIES = {"jeans", "trousers", "shorts"}
# "shoes" is both a catch-all a shopper can ask for and the label for unclassified footwear
SHOE_CATEGORIES = {"shoes", "sneakers", "heels", "flats", "sandals", "boots", "loafers"}
ONE_SIZE_CATEGORIES = {"bag"}
NON_APPAREL = SHOE_CATEGORIES | ONE_SIZE_CATEGORIES


def matches_category(category: str, wanted: list[str]) -> bool:
    """Does a product category satisfy the brief? Asking for "shoes" accepts any footwear."""
    return category in wanted or ("shoes" in wanted and category in SHOE_CATEGORIES)


COLORS = [
    "black", "white", "grey", "navy", "blue", "beige", "cream", "brown", "green",
    "olive", "red", "pink", "burgundy", "yellow",
]
STYLES = [
    "minimalist", "classic", "elegant", "casual", "streetwear", "boho", "romantic",
    "preppy", "sporty", "vintage", "cozy", "evening",
]
OCCASIONS = [
    "casual", "everyday", "office", "interview", "date", "party", "wedding-guest",
    "summer", "beach", "winter", "gym", "travel", "outdoor",
]

BRIEF_FIELDS = [
    "items_wanted", "section", "occasion", "style", "colors_liked", "colors_avoided",
    "materials_avoided", "size_top", "size_bottom", "size_shoe", "budget_total", "budget_per_item",
]


# --------------------------------------------------------------------------- #
# Idea 1: structured, editable shopping brief
# --------------------------------------------------------------------------- #
class Clarification(BaseModel):
    field: str = Field(description="Brief field this question resolves, e.g. size_top")
    question: str
    options: list[str] = Field(description="2-4 short, concrete, clickable answers")
    allow_free_text: bool = True
    # Which gulf-of-envisioning gap this question helps the shopper cross:
    # "capability" | "instruction" | "intentionality". Optional; shown in the UI + report.
    gap: Optional[str] = None


class ShoppingBrief(BaseModel):
    items_wanted: list[str] = []
    section: Optional[Literal["women", "men", "unisex"]] = None
    occasion: Optional[str] = None
    style: list[str] = []
    colors_liked: list[str] = []
    colors_avoided: list[str] = []
    materials_avoided: list[str] = []
    size_top: Optional[str] = None
    size_bottom: Optional[str] = None
    size_shoe: Optional[str] = None  # as the shopper said it: "EU 38", "UK 5", "US 7" or "38"
    budget_total: Optional[float] = None
    budget_per_item: Optional[float] = None
    notes: str = ""
    # field name -> "stated" (user said it) | "inferred" (AI guessed) | "edited" (user changed it)
    provenance: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Conversational concierge: one chat turn routed into actions over the pipeline
# --------------------------------------------------------------------------- #
ActionKind = Literal["shop", "refine", "add", "remove", "confirm_cart", "checkout", "smalltalk"]


class ConciergeAction(BaseModel):
    kind: ActionKind
    # For shop/refine: the shopping request or feedback to pass downstream.
    query: Optional[str] = None
    # For add/remove: item categories the shopper referred to, e.g. ["dress", "bag"].
    refs: list[str] = Field(default_factory=list)


class ConciergeTurn(BaseModel):
    """What the concierge decided for one user message: a reply plus the actions to run."""
    reply: str = Field(description="One or two sentences the assistant says back to the shopper")
    actions: list[ConciergeAction] = Field(default_factory=list)


class IntakeLLMOutput(BaseModel):
    """What the Intake Agent returns. Kept flat (no dicts) for structured outputs."""
    items_wanted: list[str]
    section: Optional[Literal["women", "men", "unisex"]]
    occasion: Optional[str]
    style: list[str]
    colors_liked: list[str]
    colors_avoided: list[str]
    materials_avoided: list[str]
    size_top: Optional[str]
    size_bottom: Optional[str]
    size_shoe: Optional[str]
    budget_total: Optional[float]
    budget_per_item: Optional[float]
    notes: str
    inferred_fields: list[str] = Field(description="Fields you filled by inference rather than from explicit user words")
    clarifications: list[Clarification]
    summary: str = Field(description="One-sentence restatement of what the shopper wants")


# --------------------------------------------------------------------------- #
# Stylist agent output
# --------------------------------------------------------------------------- #
class Recommendation(BaseModel):
    product_id: str
    size: Optional[str] = None
    rationale: str
    matched_preferences: list[str] = []


# --------------------------------------------------------------------------- #
# Idea 2: verifiable recommendations
# --------------------------------------------------------------------------- #
CheckStatus = Literal["pass", "warn", "fail"]


class Check(BaseModel):
    name: str
    status: CheckStatus
    evidence: str


class ClaimVerdict(BaseModel):
    claim: str
    supported: bool
    evidence: str


class ClaimCheckItem(BaseModel):
    product_id: str
    claims: list[ClaimVerdict]


class ClaimCheckOutput(BaseModel):
    items: list[ClaimCheckItem]


class VerifiedRecommendation(BaseModel):
    product: dict
    size: Optional[str]
    rationale: str
    matched_preferences: list[str]
    checks: list[Check]
    claims: list[ClaimVerdict] = []
    verdict: CheckStatus


# --------------------------------------------------------------------------- #
# Idea 3: approval-gated checkout
# --------------------------------------------------------------------------- #
class CartItem(BaseModel):
    product_id: str
    size: str
    qty: int = 1
    color: Optional[str] = None       # the colourway chosen, if any


class PlanLine(BaseModel):
    product_id: str
    name: str
    size: str
    qty: int
    unit_price: float
    line_total: float
    verdict: Optional[CheckStatus] = None
    color: Optional[str] = None
    store: Optional[str] = None


class CheckoutPlan(BaseModel):
    plan_id: str
    plan_hash: str
    lines: list[PlanLine]
    subtotal: float
    shipping: float
    tax: float
    total: float
    currency: str
    payment_method: str
    spending_cap: Optional[float]
    steps: list[str]
    warnings: list[str]
    requires_cap_override: bool
    status: Literal["awaiting_approval", "approved", "rejected", "paid", "failed", "stale"] = "awaiting_approval"
