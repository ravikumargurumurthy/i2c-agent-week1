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
