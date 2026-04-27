# Findings

## Day 1
- **[OPEN] `validate_amounts()` deduction semantics are likely wrong.**
  Current rule: `sum(amount_paid) + sum(deduction_amount) + unallocated == total`.
  In I2C convention, deductions are *informational* (booked as separate AR records),
  not part of cash reconciliation. Likely correct rule:
  `sum(amount_paid) + unallocated == total`.
  Will revisit after Day 4 eval cases — let real examples force the right model.

## Day 2
- **[OPEN] `lookup_customer` doesn't detect ambiguous matches.**
  Query "Acme" matches both CUST001 (Acme Corporation) and CUST002 (Acme Industries Inc)
  with similar scores. Current threshold-based confidence (>=80) returns the top match
  without surfacing the ambiguity. Consider adding a "gap" check: if score #1 - score #2 < 10,
  treat as ambiguous and return customer_id=None with both candidates highlighted.
  Defer fix until eval cases on Day 4 confirm this is a problem in practice.

## Day 3 (Agent loop runs)
- **[CONFIRMED] `lookup_customer` ambiguity bug from Day 2 manifests in agent.**
  Sample: "Payment $950.00 from Acme for INV-1004"
  Agent resolved to CUST001 (Acme Corporation) but INV-1004 actually belongs to
  CUST002 (Acme Industries). Confidence dropped to 0.72 only because the wrong
  customer's open AR didn't contain INV-1004. If CUST001 had had a $950 invoice
  by coincidence, this would auto-apply to wrong customer at high confidence.
  Severity: HIGH. Fix: gap-based ambiguity check in `lookup_customer`.

- **[NEW] WRatio over-scores common corporate suffixes.**
  Sample: "Payment $5,000 from Random Corp for INV-9999"
  Resolved to CUST012 (Cyberdyne Systems Corp) at score 85.5 — purely matching
  on the word "Corp". Threshold of 80 isn't strict enough to filter this.
  Options: raise threshold to 90, OR strip common suffixes before scoring,
  OR use token-set ratio that downweights generic tokens, OR require multiple
  token overlap.
  Severity: HIGH. Fix candidate for Day 5.

- **[OBSERVATION] Agent's confidence band logic works as written but the
  inputs to it are unreliable.** Confidence of 0.72 on Sample 3 was
  *correct given the (wrong) inputs* — the prompt rubric is sound; the
  customer matcher is the weak link. This validates the design choice
  to compute confidence from multiple signals rather than just the LLM's
  self-report.

## Day 4 (Eval results — short-pay convention bug surfaces)

Pass rate: 6/10

### Failures classified

- **ev_003_short_pay_with_reason** — convention bug, not extraction bug.
  Agent returned amount_paid=2400 (subtracting the $50 deduction). Eval expects 2450 (full cash).
  Root cause: schema's validate_amounts() includes deductions in reconciliation,
  which is inconsistent with canonical I2C convention. The Day 1 open question is now answered.

- **ev_004_short_pay_no_reason** — same root cause as ev_003.
  Same arithmetic pattern: amount_paid = total - deduction_amount.

- **ev_006_ambiguous_customer** — Day 3 finding #1, lookup_customer ambiguity not surfaced.
- **ev_010_unknown_payer** — Day 3 finding #2, WRatio over-scoring on "Corp".

### Decision
Adopt canonical I2C convention:
- amount_paid = cash applied to invoice (full cash for short-pays)
- deduction_amount = informational, not part of cash reconciliation
- Reconciliation: sum(amount_paid) + unallocated_amount == total_amount

Day 5 fixes:
1. Update validate_amounts() to drop deductions from sum
2. Strengthen system prompt with explicit convention statement
3. Fix lookup_customer ambiguity check (gap between top 2)
4. Fix lookup_customer scoring (strip suffixes or raise threshold)
5. Re-run evals; target 9-10/10

## Day 4 (Constraint discovered: deployment doesn't support temperature override)

Tried adding temperature=0 to API call. Azure gpt-5.3-chat deployment rejected with 400:
"Unsupported value: 'temperature' does not support 0".

This is a known constraint of newer GPT models on Azure — temperature is locked
at default. We have no direct control over sampling.

### Implications
1. Cannot enforce deterministic output via API params.
2. Boundary cases (like ev_007) may oscillate between runs — that's intrinsic
   to using this deployment, not a bug in our code.
3. Must rely on:
   - Strong procedural prompting (already in place)
   - Post-hoc Pydantic + business validation (already in place)
   - Multi-run evals to measure variance (deferred to Week 2)

### Action
- Reverted temperature=0 change.
- Defer multi-run eval harness to Week 2 cleanup.
