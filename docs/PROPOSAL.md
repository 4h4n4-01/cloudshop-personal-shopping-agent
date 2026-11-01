# CloudShop: An Agentic Clothes-Shopping Assistant
**CS 619 Project Proposal (revised).** Source of truth for the proposal; the submitted PDF is in `deliverables/`.

## Overview
CloudShop is a multi-agent clothes-shopping assistant — an **AI wrapper over a shopping backend**
(the synthetic *CloudShop Marketplace*, swappable for a real retailer's feed once licensed). A shopper
describes what they want in plain words, and cooperating agents turn that request into a **checked
shortlist** and an **approval-gated purchase**. The course asks for three ideas; this project
implements **four**, each with a user-facing component:

1. **Making intent expression easier (Week 1)**
2. **Supporting users in verifying AI output (Week 2)**
3. **Human oversight and intervention (Week 4)**
4. **Agent memory & personalisation (Week 4)** — a returning customer's history pre-fills the brief and
   biases picks, and **collaborative filtering** surfaces what *look-alike shoppers* chose; the shopper
   sees and can edit/forget what is remembered.

The multi-agent structure (Week 6) and tool use (Week 3) are the **supporting architecture** that make
the three ideas possible — not graded ideas on their own, and deliberately *not* opaque to the user:
the shopper moves between agents in the UI and can inspect everything each agent did.

## Problem and motivation
Buying clothes online mixes **hard constraints** (size, budget, materials) with **vague, taste-based
wants** ("something for a summer wedding, not too formal"). Store filters handle only the first. An LLM
agent handles both, but introduces three predictable failure modes, each of which the shopper pays for:

| Failure mode | Cost to shopper | The idea that targets it |
|---|---|---|
| **Misreads intent** — guesses size/budget/occasion wrong | Wrong shortlist, wasted time | Idea 1 (intent) |
| **Misstates facts** — "this linen dress fits your budget" when it doesn't | Lost trust, bad buys | Idea 2 (verification) |
| **Spends unintended money** — buys the wrong thing / over budget | **Irreversible** financial loss | Idea 3 (oversight) |

## The three ideas — how each feature *implements* the course idea
This is the mapping the proposal feedback asked to make explicit: each course idea → the concrete
feature → **the mechanism that realizes it** → what the shopper does.

### Idea 1 — Making intent expression easier (Week 1) · `app/agents/intake.py`
- **Feature:** free text becomes an **editable structured brief** (size, budget, occasion, colours/
  materials to avoid, …).
- **Mechanism that realizes the idea:** every field is tagged ***you said* / *AI inferred* / *you edited***
  (provenance colours), so the agent's assumptions are visible and correctable *before* they shape
  results. Gaps that would change the shortlist are asked as **2–4 clickable option chips**
  ("Size on top? S · M · L · XL"), which is cheaper than writing a better prompt and shows the answer space.
- **What the shopper does:** reads the brief, clicks a chip or edits a field; the edit propagates to
  every downstream agent (they read the brief, not the raw chat).
- **Design rules (in prompt + code):** never invent a size/budget, ask ≤3 questions, never re-ask a
  filled field, clicked chips apply deterministically (no LLM round-trip).

### Idea 2 — Supporting users in verifying AI output (Week 2) · `app/agents/verifier.py`
- **Feature:** every Stylist pick carries a **verdict badge + evidence checklist** before the shopper sees it.
- **Mechanism that realizes the idea:** (a) **hard constraints checked in plain code** (category, budget,
  size-in-stock, avoided colours/materials), each check carrying its evidence string ("$129 vs $150 budget",
  "Size M: out of stock"); (b) **claim grounding** — the Stylist's rationale is split into atomic claims and
  each is checked against the raw product record. The agent that *proposes* is never the agent that *checks*.
- **What the shopper does:** sees evidence next to each claim, expands the checklist, opens the **Verify tab**
  for the full item × check matrix. Failing picks are hidden but one click reveals them — nothing is silently dropped.

### Idea 3 — Human oversight and intervention (Week 4) · `app/agents/checkout.py`
- **Feature:** before any money moves, the Checkout agent shows a **plan** (line items, totals, exact steps,
  warnings) and **stops**.
- **Mechanism that realizes the idea:** approval is **bound to a hash of the plan** — any later change (cart,
  price, payment method) voids it and forces a fresh review; an approval **cannot be replayed**. A spending cap
  acts as a standing mandate; exceeding it needs a second explicit override. **No LLM ever holds the payment tool.**
- **What the shopper does:** reviews the plan, then **Approve / Reject / Edit cart**. Payment runs only against
  the exact approved plan.

## Supporting architecture (not graded ideas, kept user-inspectable)
- **Tool use (Week 3):** the Stylist uses `search_catalog`, `get_product`, `check_stock`, `submit`.
- **Multiple agents (Week 6):** four single-responsibility agents with narrow tool sets. Separation is a
  *safety* property (proposer ≠ checker; no agent holds payment) and is surfaced in the UI's agent-activity drawer.

## Evaluation (thorough — see `docs/EVALUATION.md`)
- **Offline (`python -m eval.run_eval`), run in both `mock` and `claude` modes:**
  - **E1 (intent):** brief-field accuracy, clarification recall, over-asking — on the 12-case set **plus a
    held-out set** written separately (the mock parser was tuned on the 12, so that score alone isn't fair).
  - **E2 (verification):** fault injection (over-budget, out-of-stock size, avoided material, wrong category,
    hallucinated claim) → recall per fault type + false-alarm rate on clean picks. **Ablation:** Claude Stylist
    *without* the Verifier — what share of raw picks break a hard constraint (motivates Idea 2).
  - **E3 (oversight):** checkout safety invariants (no charge before approval, stale approval rejected, cap
    override required, declined card releases stock, charged = shown total, no replay).
- **User study (3 participants, within-subjects, ~20 min each):** vague-request task, spot-the-error task,
  checkout task, each with the feature on vs. off. Measures: success, time, errors caught, 5-pt Likert for
  trust/control/effort, short interview.

## Scope and feasibility
The system is built (4 agents, GUI, payment plugin, eval harness, tests). Remaining work is the thorough
evaluation and writing. Scope confirmed reasonable in proposal feedback.
