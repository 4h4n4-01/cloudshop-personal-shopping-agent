"""Interface layer: the FastAPI server + GUI. It is deliberately thin - it validates requests,
delegates to the `Orchestrator` (the control plane), and serves the UI. All agent coordination
lives in `app/orchestrator.py`.

Run:  uvicorn app.main:app --reload     then open http://127.0.0.1:8000
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import memory, prompts
from .agents.checkout import CheckoutError
from .config import CLAUDE_MODEL, OLLAMA_MODEL, OPENAI_MODEL, STATIC_DIR, provider
from .llm import LLMRefusal
from .models import CartItem, ShoppingBrief
from .orchestrator import Orchestrator
from .tools import catalog
from .tools.payments import get_provider

app = FastAPI(title="CloudShop - agentic clothes shopping")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
PROVIDER = get_provider()
SESSIONS: dict[str, Orchestrator] = {}


def _get(sid: str) -> Orchestrator:
    if sid not in SESSIONS:
        raise HTTPException(404, "Session not found - reload the page.")
    return SESSIONS[sid]


def _guard(fn):
    """Map orchestrator/agent/LLM failures onto readable HTTP errors for the GUI."""
    try:
        return fn()
    except (CheckoutError, ValueError) as e:
        raise HTTPException(400, str(e))
    except LLMRefusal as e:
        raise HTTPException(422, str(e))
    except HTTPException:
        raise
    except Exception as e:  # e.g. anthropic.APIError, bad API key
        raise HTTPException(502, f"{type(e).__name__}: {e}")


# ------------------------------ meta + viewer ---------------------------- #
@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


_VIEWER_TEMPLATE = Path(__file__).resolve().parent.parent / "pipeline" / "catalog_viewer_template.html"


@app.get("/workflow")
def workflow():
    """Page 2: the agent workflow + architecture showcase (how the agents collaborate)."""
    return FileResponse(STATIC_DIR / "workflow.html")


@app.get("/data")
@app.get("/admin")
def data_page():
    """Page 3: the database - the full catalogue (and personas). Fetches /api/catalog/all live."""
    html = _VIEWER_TEMPLATE.read_text(encoding="utf-8").replace("__CATALOG_DATA__", "null")
    return HTMLResponse(html)


@app.get("/api/catalog/all")
def catalog_all():
    """The whole CloudShop Marketplace inventory, for the backend catalog viewer."""
    return catalog.all_products()


@app.get("/api/meta")
def meta():
    llm_label = {"claude": f"Claude ({CLAUDE_MODEL})", "ollama": f"Ollama ({OLLAMA_MODEL})",
                 "openai": f"{OPENAI_MODEL}", "mock": "Mock (offline, deterministic)"}[provider()]
    return {
        "llm": llm_label,
        "payment_provider": f"{PROVIDER.name} (simulated)",
        "payment_methods": PROVIDER.list_methods(),
        "catalog_source": catalog.SOURCE_NAME,
    }


@app.get("/api/suggestions")
def suggestions(seed: Optional[int] = None, n: int = 6):
    """IDEA 1 / design pattern #2 "Suggest prompting ideas": research-grounded starter
    prompts + the refinement dimensions that cross the gulf of envisioning. Same seed -> same
    chips (reproducible), new seed -> a fresh set ("deterministic yet dynamic")."""
    return {
        "starters": prompts.starter_suggestions(seed=seed, n=n),
        "dimensions": prompts.REFINEMENT_DIMENSIONS,
        "styles": prompts.PROMPT_STYLES,
        "gap_labels": prompts.GAP_LABEL,
    }


@app.post("/api/session")
def new_session():
    o = Orchestrator()
    SESSIONS[o.id] = o
    return o.state()


@app.get("/api/session/{sid}")
def get_state(sid: str):
    return _get(sid).state()


# ---------------- Conversational concierge (one chat thread) -------------- #
class ChatReq(BaseModel):
    message: str


@app.post("/api/session/{sid}/chat")
def chat(sid: str, req: ChatReq):
    return _guard(lambda: _get(sid).chat(req.message))


# --------------------------- Agent memory ------------------------ #
@app.get("/api/customers")
def customers():
    return memory.list_customers()


class CustomerReq(BaseModel):
    customer_id: Optional[str] = None


@app.post("/api/session/{sid}/customer")
def set_customer(sid: str, req: CustomerReq):
    return _guard(lambda: _get(sid).set_customer(req.customer_id))


# ------------------------------- Intake ---------------------------------- #
class IntakeReq(BaseModel):
    message: str


@app.post("/api/session/{sid}/intake")
def run_intake(sid: str, req: IntakeReq):
    return _guard(lambda: _get(sid).build_brief(req.message))


class AnswerReq(BaseModel):
    field: str
    value: str


@app.post("/api/session/{sid}/clarify")
def answer_clarification(sid: str, req: AnswerReq):
    return _guard(lambda: _get(sid).clarify(req.field, req.value))


@app.put("/api/session/{sid}/brief")
def edit_brief(sid: str, brief: ShoppingBrief):
    return _guard(lambda: _get(sid).edit_brief(brief.model_dump()))


# ----------------------- Stylist → Verifier ------------------------------ #
class RecommendReq(BaseModel):
    feedback: Optional[str] = None


@app.post("/api/session/{sid}/recommend")
def recommend(sid: str, req: RecommendReq):
    return _guard(lambda: _get(sid).recommend(req.feedback))


# -------------------------------- Cart ----------------------------------- #
@app.post("/api/session/{sid}/cart")
def add_to_cart(sid: str, item: CartItem):
    return _guard(lambda: _get(sid).add_to_cart(item))


@app.delete("/api/session/{sid}/cart/{index}")
def remove_from_cart(sid: str, index: int):
    return _guard(lambda: _get(sid).remove_from_cart(index))


# ------------------------------ Checkout --------------------------------- #
class PlanReq(BaseModel):
    spending_cap: Optional[float] = None
    payment_method: str = "pm_card_visa"


@app.post("/api/session/{sid}/checkout/plan")
def plan_checkout(sid: str, req: PlanReq):
    return _guard(lambda: _get(sid).plan_checkout(req.spending_cap, req.payment_method))


class ApproveReq(BaseModel):
    plan_id: str
    cap_override: bool = False


@app.post("/api/session/{sid}/checkout/approve")
def approve_checkout(sid: str, req: ApproveReq):
    return _guard(lambda: _get(sid).approve(req.plan_id, req.cap_override))


@app.post("/api/session/{sid}/checkout/reject")
def reject_checkout(sid: str, req: ApproveReq):
    return _guard(lambda: _get(sid).reject(req.plan_id))


# ---------------- Hardened multi-gate secure checkout (Idea 3) ----------- #
class SecureBeginReq(BaseModel):
    spending_cap: Optional[float] = None
    payment_method: str = "pm_card_visa"


class CardReq(BaseModel):
    payment_method: str


class ReviewReq(BaseModel):
    cap_override: bool = False


class OtpReq(BaseModel):
    code: str


@app.post("/api/session/{sid}/secure/begin")
def secure_begin(sid: str, req: SecureBeginReq):
    return _guard(lambda: _get(sid).begin_secure_checkout(req.spending_cap, req.payment_method))


@app.post("/api/session/{sid}/secure/confirm-cart")
def secure_confirm_cart(sid: str):
    return _guard(lambda: _get(sid).sc_confirm_cart())


@app.post("/api/session/{sid}/secure/confirm-card")
def secure_confirm_card(sid: str, req: CardReq):
    return _guard(lambda: _get(sid).sc_confirm_card(req.payment_method))


@app.post("/api/session/{sid}/secure/confirm-review")
def secure_confirm_review(sid: str, req: ReviewReq):
    return _guard(lambda: _get(sid).sc_confirm_review(req.cap_override))


@app.post("/api/session/{sid}/secure/otp")
def secure_otp(sid: str, req: OtpReq):
    return _guard(lambda: _get(sid).sc_submit_otp(req.code))


@app.post("/api/session/{sid}/secure/finalize")
def secure_finalize(sid: str):
    return _guard(lambda: _get(sid).sc_finalize())


@app.post("/api/session/{sid}/secure/reject")
def secure_reject(sid: str):
    return _guard(lambda: _get(sid).sc_reject())
