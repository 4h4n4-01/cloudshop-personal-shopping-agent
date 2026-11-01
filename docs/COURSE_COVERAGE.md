# Course coverage — how CloudShop applies each topic

This maps the CS619 (Human–AI Design) course material, week by week, to concrete features of
CloudShop, so every design decision traces back to a taught concept. Quiz-pack references (e.g.
W2-15) point to the specific idea. CS614's Agentic-RAG topic is included because it grounds the
retrieval evaluation (E5).

The four **graded ideas** are: **Idea 1 Intent** (W1), **Idea 2 Verification** (W2), **Idea 3
Oversight** (W4), **Idea 4 Memory** (W4). Weeks 3 and 6 (tools, context, planning, multi-agent,
evaluation) are the *supporting architecture and evaluation* the course asks for.

---

## Week 1 — Text interaction, envisioning, metacognition → Idea 1 (Intake + Concierge)

| Concept | How CloudShop applies it | Where |
|---|---|---|
| Command vs **intent-based** interaction (W1-5/6) | You state an outcome ("clubby but comfy"); the agent infers the actions | `concierge.py`, `intake.py` |
| **Gulf of envisioning** — capability / instruction / intentionality gaps (W1-37) | Every clarifying question carries a `gap` tag, shown as a UI badge | `models.Clarification.gap`, `intake.missing_clarifications` |
| **Why Johnny Can't Prompt** — over-generalization, **social lens / CASA**, vague instructions (W1-11/18) | Intake normalizes prompt styles: strips politeness, maps vague adjectives ("classy"→elegant), routes negations, captures feelings ("not too tight") | `intake._ADJ_STYLE`, `_NOTE_CUES`, `prompts.PROMPT_STYLES` |
| **Six design patterns**, esp. #2 "Suggest prompting ideas" (W1-47/49) | Research-grounded starter chips + refinement dimensions, each tagged by gap | `prompts.py`, `GET /api/suggestions` |
| Pattern #4 "Make output explainable" | The Verifier checklist links each claim to its evidence | `verifier.py` |
| Pattern #6 "Allow manual control" + **metacognition: don't silently rewrite** (W1-76) | Inferred brief fields are surfaced, colour-coded and editable — never hidden | `static/app.js` brief form, provenance |

---

## Week 2 — Appropriate reliance, verification, oversight foundations → Ideas 2 & 3

| Concept | How CloudShop applies it | Where |
|---|---|---|
| **Appropriate reliance** / reliance matrix (W2-13/15) | Verifier + checkout gates let the shopper reject or correct the AI — "correct self-reliance" | `verifier.py`, `secure_checkout.py` |
| **Self-verification is weak** (W2-4, Mata v. Avianca) | Claims are checked against an **independent source** (the product record), not by re-asking the model | `verifier._claim_check` |
| **Calibrated trust vs appropriate reliance** (W2-14) | The checklist discriminates per-item (pass/warn/fail), not a blanket trust score | `models.Check` |
| **Verification effort / explanation value** (W2-48/49) | Each pick's checklist makes contradictions cheap to find (claim ↔ evidence) | `verifier.constraint_checks` |
| **Confidence: two jobs — communicate & allocate** (W2-45) | The verdict both informs the shopper and routes high-stakes payment to a human | verdicts + `secure_checkout` |
| **Approval before consequential action** (Abridge gating, W2-59) | Nothing is charged until the shopper approves; payment is never exposed to the LLM | `secure_checkout.py` |
| **Cognitive forcing function** (Buçinca, W2-62) | A combined cart+card+amount re-confirmation gate breaks AI anchoring | `secure_checkout` gate 3 |
| **Deferral / allocate on high stakes** (W2-55/61) | 3-D Secure OTP + 2FA — a factor the agent cannot produce, so it can never self-pay | `card_network.verify_3ds` |

---

## Week 3 — Agents, tools, context, planning, governance → the architecture

| Concept | How CloudShop applies it | Where |
|---|---|---|
| **Agent = LLM using tools in a loop** (W3-2); autonomy coexists with guardrails (W3-3) | The Stylist runs a tool loop; autonomy is bounded by guardrails and approval | `harness.run_tools` |
| **Agent loop** steps (W3-4) and **five trace elements** (W3-5) | Every step (task, action, observation, state, exit) is logged and inspectable on `/workflow` | `trace.py`, `static/workflow.html` |
| **Three tool categories** — retrieve / compute / change (W3-8) | `search_catalog`/`check_stock` (retrieve), size conversion (compute), `reserve`/`capture` (change) | `tools/catalog.py`, `tools/payments/` |
| **Describing an action ≠ performing it** (W3-7) | The Verifier checks the product record, and payment confirms the *actual* capture result — not the agent's say-so | `verifier.py`, `secure_checkout.finalize` |
| **Harness** assembles context, executes tools, controls the loop (W3-15/16) | A dedicated harness runs the tool loop separately from the model layer | `harness.py` vs `llm.py` vs `tools/` |
| **Instructions vs task** (W3-18) | Stable agent `SYSTEM` prompts vs the per-turn brief/message | each agent's `SYSTEM` |
| **Compass vs railway-tracks** spec (Petridis, W3-21) | The brief is a "compass" (outcome + constraints, room for judgement); the checkout plan is "railway" where stakes demand it | `intake.py`, `checkout.build_plan` |
| **Delegation assumptions & supports** (W3-26) | "Knows our project" → explicit constraints in the brief; "reads between the lines" → inspectable plan; "notices & asks" → clarifications; "absorbs everything" → the Stylist *searches* the 1,201-item catalog rather than ingesting it | brief, clarifications, `search_catalog` |
| **Prompt vs context engineering** (W3-33/36) | The Concierge passes a *selected* context per turn (brief, on-screen categories, cart, checkout state) — not the whole history or catalog | `orchestrator.chat` ctx dict |
| **Planning: a plan's contents + status ≠ success** (W3-40/41) | The checkout plan lists steps, totals and warnings with a "done-when"; a status label never stands in for the actual capture result | `checkout.build_plan`, `secure_checkout` |
| **Co-planning / co-execution** (Cocoa, W3-46) | Curating the cart by chat is editable, stepwise co-execution with the agent | concierge + chat dock |
| **Autonomy tiers** (Dayos, W3-50) & **action space vs autonomy** (W3-49) | Browsing = high autonomy / small effect; **payment = Tier 3**: the agent proposes, the human approves, 2FA is human-only | `secure_checkout.py` |
| **IMDA agentic-AI governance** (W3-48) | Bounded risk, meaningful human accountability (approval gates), technical controls (guardrails), audit trail | guardrails + approval + trace |

---

## Week 4 — Human oversight, guardrails, memory → Idea 3 & Idea 4

| Concept | How CloudShop applies it | Where |
|---|---|---|
| **Clarify / inform / approve** (W4-1) | Clarify = Intake chips; approve = checkout gates; inform = the agent asks when a pick can't be verified | `intake`, `secure_checkout`, concierge |
| **Instructions vs guardrails vs approval** (W4-10) | All three are present and *distinct* | see below |
| — **Input guardrail** (W4-12) | An out-of-scope request ("book a flight") is rejected at the front door | `guardrails.check_in_scope` |
| — **Output guardrail** (W4-13) | A pick that failed a hard check can never be added to the cart | `guardrails.check_recommendation_addable` |
| — **Tool guardrail** (W4-14) | The payment call is rejected for a bad amount / unknown card / over a hard ceiling | `guardrails.check_payment` |
| — **Availability guardrail** (W4-13) | A named colour/size no on-screen option can meet is reported ("not available in pink / size 44, these come in…"), never silently substituted | `guardrails.check_add_satisfiable` |
| — **Identity guardrail** (W4-13) | A brand/product the shopper names that isn't among the options is reported with the real options listed, not silently replaced with a different brand (and no fabricated reason) | `guardrails.unmatched_specifiers` |
| **A real gate is application-enforced, not an instruction** (W4-16 trap) | The guardrails run in code and the checkout gates hold state — the model cannot talk past them | `guardrails.py`, `secure_checkout.py` |
| **Approval binds to a version/hash; changes need re-review** (W4-20) | Each gate binds a hash of what was shown; any cart/card change voids downstream approvals | `secure_checkout._cart_sig` |
| **What a reviewer needs to see** (W4-21) | The plan shows the proposed action, impact (total), evidence (verifier verdicts) and warnings | `checkout.build_plan`, UI |
| **Chen et al. intervention failure modes** (W4-25) | We design against all three: we gate **before** the effect (not "too late"); the combined review forces a content check (not "procedure over judgement"); per-gate questions + cognitive forcing fight "approval without reconsideration" | `secure_checkout` gates + `RELIANCE_QUESTIONS` |
| **Short- vs long-term memory; remember / recall / update** (W4-30/34) | A returning shopper's profile is derived from choice history and recalled to bias the Stylist | `memory.py` (Idea 4) |
| **Selective recall = context engineering** (W4-41) | Only the signed-in shopper's profile enters context, not every customer record | `memory.profile`, `list_customers` |

> Note for the report: Idea 4 currently derives memory from history rather than using explicit
> `remember/recall/update` *tools* with named records and the "from now on" vs "just this time"
> distinction (W4-39). That is the one clean extension if you want a literal match to W4's memory
> tool-loop; see *Future work*.

---

## Week 6 — Multi-agent systems & evaluation → the pipeline + E1–E5

| Concept | How CloudShop applies it | Where |
|---|---|---|
| **Agent team: specialization** (W6-2) | Four narrow specialists (Intake, Stylist, Verifier, Checkout) + a Concierge router | `app/agents/` |
| **Coordination: fixed specialist workflow** (W6-9) | The pipeline order is app-specified: Intake → Stylist → Verifier → Checkout | `orchestrator.py` |
| **A mandatory check is an explicit workflow step, not the manager's choice** (W6-13) | The Verifier **always** runs after the Stylist — it is wired in, not left to a router to skip | `orchestrator.recommend` |
| **Manager delegation / handoff** (W6-10/11) | The Concierge routes each message to the right worker | `concierge.route` |
| **Separate histories / pass selected findings** (W6-7) | Each agent has its own system prompt; the orchestrator passes selected results, not full transcripts | per-agent `SYSTEM`, orchestrator |
| **Define an observable requirement, not "be helpful"** (W6-17/30) | Each experiment targets a concrete, inspectable requirement | `eval/run_eval.py` |
| **Code judge + answer key** (W6-20/23) | E1 field accuracy vs gold brief; E2 fault recall; E5 precision/recall | E1, E2, E5 |
| **LLM judge + rubric** (W6-21/25) | At runtime in Claude mode, the Verifier's claim grounding is an LLM critic with a rubric. **It is not the judge behind the reported E5 number**: offline (mock) runs ground claims with a keyword heuristic, so E5 faithfulness is a heuristic check. A Claude-mode E5 must use a *separate* judge model, because there the Verifier would be grading its own output | `verifier._claim_check`, E5 |
| **Human judge / user study + think-aloud** (W6-22/33) | The 3-person study uses realistic tasks and the neutral "what is on your mind?" prompt | `docs/EVALUATION.md` |
| **Test cases ≠ user studies** (W6-31/32) | Offline tests check the agent; the user study checks whether *people* can use it | `tests/`, `docs/EVALUATION.md` |

---

## CS614 — Agentic RAG → retrieval correctness (E5)

| Concept | How CloudShop applies it | Where |
|---|---|---|
| **Agentic RAG** — retrieval as a tool the agent drives (§8) | The Stylist drives catalog retrieval as a tool | `tools/catalog.search_catalog` |
| **RAGAS: faithfulness, context precision/recall** (§41) | E5 scores all three. Faithfulness reuses the Verifier's grounding check (98% grounded, offline **keyword heuristic**); because it is the same check the product runs, it shows consistency rather than independent truth | `eval/run_eval.e5_retrieval_correctness` |
| **Structured data → filter/SQL retrieval, not cosine** (§36/47) | We retrieve by hard filters — avoiding the cosine-RAG failure modes, and we justify why | `catalog.search_catalog`, E5 notes |
| **Retrieved text is untrusted; keep a fixed baseline** (§50/51) | The Verifier guards the retrieved record; the filter retriever is kept as the baseline the agent must beat | `verifier.py`, E5 |

---

## Future work (clean, course-aligned extensions, not yet built)
- **Explicit memory tool-loop** (W4-34/39): `remember/recall/update` as named-record tools, with
  "from now on" → `update` vs "just this time" → no store.
- **Structurally-enriched risk label** (W4-25): a localized "⚠ risky" tag on the highest-stakes
  checkout line, to raise scrutiny at the gate.
- **Explicit `update_plan` tool** (W3-42) for multi-item shopping, with a living plan on `/workflow`.
- **Parallel agents** (W6-2) for multi-item requests (dress + shoes investigated simultaneously).
