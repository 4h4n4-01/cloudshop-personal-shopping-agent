# Evaluation plan

The rubric allows offline benchmarks, a small user study (2–3 people), or both. This plan
does both: `eval/run_eval.py` gives numbers for each idea, and a short study tests whether
the interface features actually help people.

## Offline (`python -m eval.run_eval`)

| Exp. | Idea | Method | Metrics |
|---|---|---|---|
| E1 | Intent | 12 **tuned** requests (`eval/cases.json`) + 16 **held-out** requests (`eval/holdout_cases.json`), each with gold brief fields and the gaps that *should* be asked | field accuracy, clarification recall, over-asking — reported per set |
| E2 | Verification | Fault injection over 1,201 products: corrupt clean picks with 8 known errors (over budget, out-of-stock size, avoided material, wrong category, hallucinated claim, false fabric / care / origin claim) | recall per fault type, false-alarm rate on clean picks |
| E2b | Verification (ablation) | **No-Verifier ablation**: let the Stylist pick, use the Verifier only to *judge* (not gate), and count what would reach the shopper without it | raw picks breaking a hard constraint; raw picks with an unsupported claim; injected faults a no-gate pipeline ships |
| E3 | Oversight | Safety invariants of checkout | no charge before approval; stale approval rejected; cap override required; declined card releases stock; charged amount = shown total; no replay |
| E4 | Memory / personalisation | For each of 8 featured customers, request a bare category with vs. without their remembered profile; a population of 250 shoppers drives collaborative filtering | preferred-attributes per pick (match depth), avoided-attribute leakage, collaborative coverage |
| E5 | Retrieval correctness (RAGAS-style) | CloudShop is **agentic retrieval over a structured catalog** (CS614 Topic 8, §8), so retrieval is scored as the deck prescribes (§40–41). The hard-filter tool `search_catalog` is the retriever *and* the fixed-pipeline baseline; ground-truth relevant set = all hard-filter passers | context precision, context recall@k, dropped-relevant (real miss), answer relevance, **faithfulness** (grounded claims, via the Verifier), grounded-answer rate |

**Before reporting:**
- Run every experiment in both modes (`LLM_MODE=mock` and `LLM_MODE=claude`). The Claude-vs-rules
  comparison on the held-out set is a useful finding in itself, and E2b/E5 only become telling under
  a real LLM Stylist.
- Raise n on the thin fault types (`avoided_material`, `false_care_claim`).

## User study (3 participants, about 20 minutes each, within-subjects)

**Tasks** (counterbalanced order):
1. *Vague request.* "Find something to wear to a friend's summer wedding." Condition A: brief
   form + chips. Condition B: chat only (hide the brief and chips).
2. *Spot the problem.* The study build seeds one bad pick (e.g. out of stock in their size,
   or a false material claim). Condition A: verifier checklist visible. Condition B: hidden.
3. *Checkout.* Buy an outfit under a set budget. After the plan is shown, the experimenter
   changes the cart from a second tab (or the plan includes an over-cap item). Does the
   participant notice, and does the system stop the payment?

**Measures:** task success, time, whether the seeded error was caught, 5-point Likert for
trust / control / effort (NASA-TLX-lite), and a short interview ("What did the colours on
the brief mean to you?", "Would you let it pay without asking?").

**Report:** a table per task (A vs B), a few quotes, and insights, including negative ones
(e.g. "participants ignored the amber inferred fields").

---

## Results — mock baseline (run 2026-10-27, offline, `LLM_MODE=mock`, 1,201-product catalog)

Reproduce: `docker run --rm -e LLM_MODE=mock cloudshop python -m eval.run_eval`
(24/24 end-to-end tests also pass.)

| Exp. | Metric | Result |
|---|---|---|
| E1 intent (tuned, n=12) | field accuracy · clar. recall · over-asking | **100%** · 92% · 0 |
| E1 intent (**held-out, n=16**) | field accuracy · clar. recall · over-asking | **85%** · 91% · 5 |
| E2 verification (8 fault types) | overall recall | 58/58 = **100%** (over_budget 7/7, out_of_stock_size 2/2, avoided_material 2/2, wrong_category 9/9, hallucinated_claim 12/12, false_fabric_claim 12/12, false_care_claim 2/2, false_origin_claim 12/12) |
| E2 verification | false alarms on clean picks | **0/42** |
| E2b **no-Verifier ablation** | raw picks breaking a hard constraint · raw picks with an unsupported claim | **0/42** · **4/42** |
| E2b ablation | injected faults a no-gate pipeline ships vs. with Verifier | **58/58 ship** without it → **0 reach the shopper unflagged** with it |
| E3 oversight | safety invariants | **6/6 PASS** (no charge before approval · stale approval rejected · cap override required · declined card releases stock · charged = shown total · no replay) |
| E4 memory | preferred attributes / pick | without memory **3.2** → with memory **4.0** (+0.9) |
| E4 memory | avoided-attribute leakage (top-4) | without memory **5/24** → with memory **0/24** |
| E4 memory | collaborative coverage | **17/24** top picks chosen by look-alike shoppers |
| E5 retrieval (RAGAS, n=12, k=15) | context precision · dropped-relevant (real miss) | **100%** · **0** |
| E5 retrieval | context recall@15 (raw) | **42%** — pure k-cap truncation (mean 232 matches/brief), not error |
| E5 retrieval | answer relevance (on-brief) · grounded-answer rate | **42/42** · **42/42** |
| E5 retrieval | **faithfulness** (rationale claims supported by product data; judged by the Verifier's **keyword heuristic**, not an LLM) | **232/236 = 98%** |

**Read these honestly.** The tuned E1 100% is the *ceiling of the deterministic baseline* — the parser
was written against those 12 cases. The **held-out 85%** is the fair number, and the 15-point gap (plus
5 over-asks on unseen phrasings) is itself a finding that motivates the Claude agent. E2's 100% shows
the checks are sound on injected faults, but n is still small on some fault types.

**On E2b (ablation).** The mock Stylist filters deterministically, so it breaks **0/42** hard
constraints on its own — but **4/42** of its picks still carry a rationale claim the product data
doesn't support, which only the Verifier catches. The headline framing: errors a real Stylist can make
(E2's 58 injected faults) would *all* ship in a no-gate pipeline; with the Verifier, **none reach the
shopper unflagged**. The ablation's intrinsic-error number becomes most telling under `LLM_MODE=claude`,
where the Stylist writes its own rationale and can over-reach.

**On E5 (retrieval correctness).** CloudShop is **agentic retrieval over structured data**, not vector
RAG, so it sidesteps the embedding-RAG failure modes (SAGE chunking, lost-in-the-middle, cosine noise,
CS614 Topic 8 §3). A structured filter has **context precision ≈ 100% by construction** (it cannot
return an off-brief item) and **zero genuine misses** — the only recall loss is the top-k cap, which is
expected truncation, not a defect. The metric that actually governs correctness is **faithfulness**
(98% of claims grounded), and it is enforced live by the Verifier — i.e. the RAGAS grounding check is
already a shipping feature, not just an offline score. The deterministic filter is kept as the
fixed-pipeline baseline the agent must earn its cost against (deck §50).

**What judged faithfulness.** In mock mode the Verifier grounds claims with a **keyword heuristic**
(`verifier._heuristic_claims`), not an LLM, so the 98% is a heuristic check, not an LLM-as-critic score.
It is also the *same* check the product runs, so it shows the Stylist's claims agree with that check,
not independent truth (a claim phrased outside the heuristic's vocabulary is neither caught nor
counted). In Claude mode the Verifier becomes an LLM critic, which makes it both the system under test
and the judge; a Claude-mode faithfulness number therefore needs a **separate judge model** (ideally a
different model family, scored against a small hand-labelled sample to check the judge itself).

## Done (2026-10-27)
- ✅ **Held-out E1 set** (`eval/holdout_cases.json`, 16 fresh requests) — tuned 100% vs held-out 85%.
- ✅ **Richer verification**: catalog expanded to 1,201 products across 12 synthetic retailers with `fabric_composition`, `care`,
  `origin`, `rating`, `returnable`, `original_price`; Verifier gained a returns check, a price-drop
  note, and claim grounding for fabric/care/origin/sale. Three new E2 fault types exercise them.

## Done (2026-10-28)
- ✅ **Hardened payment gateway (Idea 3)**: Visa/Mastercard-rails `CardNetwork` (authorize → 3-D
  Secure OTP/2FA → capture → void) + a five-gate human-in-the-loop state machine
  (`app/agents/secure_checkout.py`). **9 guardrail tests** (`tests/test_secure_checkout.py`): no
  charge before all gates, no stage-skip, OTP required, 3 wrong OTPs void the hold, cart-change
  resets the hash binding, spending-cap override, declined card rejected at auth, no replay after
  capture. Each gate maps to a Week-2 concept (see docs/DESIGN.md). Total suite now **33/33**.
- ✅ **E2b no-Verifier ablation** wired (`eval/run_eval.py::e2_ablation`): 0/42 constraint breaks,
  **4/42 unsupported claims**, 58/58 injected faults ship without the gate.
- ✅ **E5 retrieval correctness (RAGAS-style)** wired (`e5_retrieval_correctness`): context precision
  100%, 0 real misses, **faithfulness 98%** (keyword-heuristic judge in mock mode) — frames CloudShop
  as agentic retrieval over structured data; faithfulness reuses the Verifier's grounding check
  (CS614 Topic 8 mapping).

## Remaining for a *thorough* evaluation
1. **Claude-mode runs** of E1 + E2 + E2b + E5 (`LLM_MODE=claude`, needs `ANTHROPIC_API_KEY`; E5
   faithfulness must then be scored by a separate judge model, not the Verifier grading itself) — the
   Claude-vs-rules comparison on the held-out set is the key motivation for the designs, and E2b/E5's
   intrinsic-error and retrieval-recall numbers only bite once the LLM Stylist authors its own
   rationale and search query.
2. **Raise n on thin fault types** (avoided_material, false_care_claim currently 2 each).
3. **User study (3 participants)** per the protocol above — the only test of whether the *interface*
   helps people, which is what the course weights most.
