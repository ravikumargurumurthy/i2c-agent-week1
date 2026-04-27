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
