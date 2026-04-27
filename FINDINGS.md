# Findings

## Day 1
- **[OPEN] `validate_amounts()` deduction semantics are likely wrong.**
  Current rule: `sum(amount_paid) + sum(deduction_amount) + unallocated == total`.
  In I2C convention, deductions are *informational* (booked as separate AR records),
  not part of cash reconciliation. Likely correct rule:
  `sum(amount_paid) + unallocated == total`.
  Will revisit after Day 4 eval cases — let real examples force the right model.
