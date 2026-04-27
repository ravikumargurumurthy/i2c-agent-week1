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

### Day 5 follow-up: ev_006 fix tuning
Initial fix used strict `<` for gap check. Acme Corp vs Acme Industries
scored exactly 100 vs 90 — a gap of 10, which `< 10` does not catch.
Changed to `<= 10`. Logged as a reminder: boundary thresholds in fuzzy
matching need to be `<=` not `<` because real data lands on exact gaps.

## Day 5 — Bug fixes (4 iterations to reach 10/10)

### Fix 1 — Suffix stripping (Bug B from Day 3)
Added `GENERIC_SUFFIXES` set ("corp", "inc", "ltd", etc.) stripped from both
the query and customer names before fuzzy matching. "Random Corp" no longer
scores high against "Cyberdyne Systems Corp" because "Corp" no longer counts
as match evidence.

Result: ev_010 ✓.

### Fix 2 — Iteration log for Bug A (ambiguity detection)

This bug took **four iterations** to fix. Each iteration revealed a deeper
layer of the problem. Documented in detail because the iteration cycle
itself is the lesson.

**Iteration 1: gap-based ambiguity check (`< 10`)**

Added: `ambiguous = (top_score - runner_up_score) < 10`. If top 2 candidates
score within 10 points, return `customer_id=None`.

Result: ev_006 still failed. The Acme Corp vs Acme Industries gap was
*exactly 10* (100 vs 90), and `< 10` strict less-than missed it.

**Iteration 2: relax to `<= 10`**

Changed to `<= 10` to catch gap-of-10 cases.

Result: ev_006 passed but **broke 7 previously-passing cases** (ev_001-004,
007-009 — all Acme Corporation queries). The Acme Industries runner-up scored
90 against any Acme Corp query, triggering false ambiguity even on fully-specified
names like "Acme Corporation".

Lesson: gap threshold alone can't distinguish "ambiguous query" from
"fully-specified query that shares a token with another customer".

**Iteration 3: exact-match override + customer master cleanup**

Two changes:
1. Removed standalone `"Acme"` alias from CUST001 in `customers.json`.
   Real-world principle: customer masters should not have aliases that
   are ambiguous between two customers.
2. Added exact-match override in `lookup_customer`: if the query exactly
   matches one of the top customer's names/aliases (case-insensitive),
   bypass the gap-based ambiguity check. Reverted gap check to strict `<`.

Initially declared 10/10. **Re-running pytest revealed ev_006 still failing.**
Documentation error — claimed resolution before verifying with the suite.

**Iteration 4: widen gap to `<= 15`**

Diagnosed why ev_006 still failed: suffix stripping interacts with single-word
queries. `_strip_suffixes("Acme Corp")` → "acme"; `_strip_suffixes("Acme")` → "acme".
After stripping, both look identical → score 100, not the expected partial match.

This inflated the apparent score gap while the underlying ambiguity remained.
Resolution: widen the gap threshold to `<= 15`. The exact-match override
prevents legitimate full-name queries from being flagged false-ambiguous,
so a wider gap is safe.

Final logic:
- Exact-match → resolve confidently regardless of gap
- Otherwise, gap > 15 → resolve
- Otherwise → ambiguous, return `None`

Result: 10/10 ✓.

### Lessons from this multi-iteration debug
- **Defensive heuristics interact in non-obvious ways.** Suffix stripping
  (which fixed Bug B) interacted with the gap rule (designed for Bug A) to
  inflate scores in a third class of input — single-word ambiguous queries.
  Each fix was correct in isolation; combined they created a new edge case.
- **Multi-part fixes are easy to half-apply.** Iteration 3 required both a
  code change and a data change. Half-applying either one defeated the fix.
  The eval suite is the only valid signal that a multi-part change actually
  landed.
- **Threshold tuning has limits.** When a single rule can't separate two
  cases that should behave differently, you need either smarter heuristics
  or cleaner data — often both.
- **Verify before you declare victory.** Iteration 3's premature "fixed!"
  was caught immediately by re-running pytest. Without the suite, this would
  have been a silent regression discovered weeks later.

### Customer master change
Removed `"Acme"` from CUST001's aliases. Justification: an alias should
uniquely identify a customer. "Acme" cannot uniquely identify CUST001 when
CUST002 is also commonly called "Acme". Cleaning the master data is part
of the fix, not a separate concern.

---

## Open for Week 2+

- Multi-run eval harness — run each case 5-10x, report pass-rate distribution.
  Necessary for finance-grade evals given LLM non-determinism.
- LangGraph rewrite — explicit state machine instead of in-loop control flow.
  Same eval suite should still pass 10/10.
- Persistent audit log — replace stdout `verbose=True` with append-only
  Postgres table.
- Multi-format input — PDF, Excel, EDI 820 parsers feeding the same agent.
- Confidence calibration analysis — is `0.95` actually 95% accuracy in
  practice? Requires ~100+ labeled cases to measure.
- HITL review UI — Streamlit interface for low-confidence cases.

---

## Week 1 retrospective

### What I learned
- **Schema-first design is non-negotiable.** Pydantic schemas saved hours
  of debugging by forcing clarity early.
- **Tool descriptions are prompt engineering.** Single biggest reliability
  improvements came from rewriting tool descriptions, not the system prompt.
- **Eval failures fall into 3 buckets:** real bugs, bad evals, convention
  disagreements. Triage discipline matters more than fix speed.
- **LLM non-determinism is a real constraint, not a tutorial detail.** Some
  models don't even let you set temperature.
- **Defensive heuristics interact.** You can't reason about them one at a time.
  The eval suite is what makes interaction bugs catchable.

### What I'd do differently
- Spend longer on Day 1 schema design. The deduction-semantics question
  surfaced 3 days later as a real bug — an extra hour on Day 1 would have
  caught it.
- Write 2-3 eval cases on Day 1 alongside the schema. They'd have anchored
  later design decisions.
- Use a real logger from the start instead of `verbose=True`. Cheap to add
  later but builds the habit.

### Skills I now have that I didn't 5 days ago
- Building a tool-calling agent loop from scratch (no framework)
- Pydantic v2 with custom validators and cross-field business rules
- Designing eval sets that surface real failure modes
- Triaging "is this an agent bug or an eval bug?"
- Writing tool schemas that constrain LLM behavior
- Iterating on threshold-based heuristics through evals
- Diagnosing interaction bugs between defensive heuristics

### What's blocking me from production-grade work today
- No multi-run eval methodology (single-shot only)
- No persistent audit log (stdout only)
- No real database backend (JSON files)
- No HITL review UI
- No observability or tracing infrastructure

(Each of these is on the Week 2-12 roadmap.)
