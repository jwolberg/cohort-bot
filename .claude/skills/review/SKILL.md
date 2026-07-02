---
name: review
description: Validate implementation against spec with strict, file-based output
---

Review this implementation: $ARGUMENTS

Input:
- /docs/spec.md (REQUIRED)
- /docs/implementation.md (REQUIRED)

Write output to:
- /docs/review.md

Goal:
Ensure the implementation correctly satisfies the defined spec with minimal complexity and no hidden risks.

---

## Task

1. Validate implementation against acceptance criteria
2. Identify correctness issues
3. Identify edge cases not handled
4. Evaluate simplicity and maintainability
5. Identify user-facing risks

---

## Required Output Format (/docs/review.md)

# Review Results

## Acceptance Criteria Validation
For EACH acceptance criterion in /docs/spec.md:

- Criterion:
- Status: PASS / FAIL
- Notes:

---

## Critical Issues (Must Fix)
- Bugs
- Incorrect behavior
- Violations of acceptance criteria

---

## Edge Cases
- Missing edge case handling
- Incorrect assumptions

---

## Simplicity & Maintainability
- Unnecessary complexity
- Over-engineering
- Violations of existing patterns

---

## User-Facing Risks
- UX confusion
- Failure states
- Performance concerns

---

## Suggested Improvements (Optional)
- Clearly NON-REQUIRED improvements
- Must NOT expand scope

---

## Quick Wins
- Small, high-impact fixes

---

## Summary
- Overall status: PASS / FAIL
- Ready for production: YES / NO

---

## Rules

- Validate STRICTLY against /docs/spec.md
- Do NOT introduce new scope
- Do NOT suggest new features
- Prefer simplest working solution
- Be explicit and concrete
- Use only the provided inputs
- Do NOT rely on prior conversation

---

## Behavior

- If acceptance criteria are incomplete, note it but proceed
- If implementation.md is vague, infer cautiously and flag uncertainty
- Prioritize correctness over style

---

After writing:
- Confirm file created: /docs/review.md
- STOP