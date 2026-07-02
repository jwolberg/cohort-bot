---
name: build-feature
description: Run the full product workflow from scope to implementation and review with strict file-based handoffs
---

Run the product team workflow for this feature request: $ARGUMENTS

You MUST follow a file-driven workflow. Each step writes its output to disk and uses ONLY the required inputs.

All files are stored in: /docs/

---

## Step 1 — product-strategist

Input:
- Feature request: $ARGUMENTS

Task:
Define a STRICT, MINIMAL spec.

Output (write to /docs/spec.md):
- User problem
- Smallest viable solution (clearly scoped)
- Acceptance criteria (testable, explicit)
- Non-goals (strict exclusions)
- Constraints (technical or product constraints)
- Definition of Done

Rules:
- Keep scope minimal
- Avoid future features
- Be explicit and testable
- This spec is FINAL unless explicitly revised

After writing:
- Confirm file created: /docs/spec.md
- STOP

---

## Step 2 -- Plan

Input:
- /docs/spec.md (REQUIRED)
- /docs/ux.md (OPTIONAL, clarification only)

Write output to:
- /docs/BUILD_PLAN.md

Goal:
Translate the product spec into a durable, execution-ready build plan that can survive session resets and guide implementation one phase/ticket at a time.

Rules:
- Do NOT expand scope
- Do NOT introduce new features
- Prefer simplicity

After writing:
- Confirm file created: /docs/BUILD_PLAN.md
- STOP

## Step 3 — ux-designer

Input:
- /docs/spec.md ONLY

Task:
Review UX strictly against the spec.

Output (write to /docs/ux.md):
- UX issues (only if they affect clarity or usability)
- UX adjustments (minimal only)
- Updated acceptance criteria (ONLY if clarification is required)

Rules:
- Do NOT expand scope
- Do NOT introduce new features
- Prefer simplicity

After writing:
- Confirm file created: /docs/ux.md
- STOP

---

## Step 4 — software-engineer

Input:
- /docs/spec.md
- /docs/ux.md (if exists)

Task:
Implement EXACTLY what is defined.

Output (write to /docs/implementation.md):
- Approach
- Code changes
- Files modified/created
- Mapping: each acceptance criterion → implementation
- How to run/test

Rules:
- Do NOT expand scope
- Do NOT refactor unrelated areas
- Reuse existing patterns
- Keep implementation minimal

After writing:
- Confirm file created: /docs/implementation.md
- STOP

---

## Step 5 — code-reviewer

Input:
- /docs/spec.md
- /docs/implementation.md

Task:
Validate implementation strictly against spec.

Output (write to /docs/review.md):
- Pass/fail per acceptance criterion
- Bugs / edge cases
- Inconsistencies with spec
- Critical fixes (required)
- Optional improvements (clearly labeled)

Rules:
- Focus on correctness
- Do NOT introduce new scope

After writing:
- Confirm file created: /docs/review.md
- STOP

---

## Global rules

- Scope is LOCKED after Step 1
- No agent may expand scope
- Prefer simplest working solution
- Avoid premature optimization
- Each step MUST only use its defined inputs
- Do NOT rely on prior conversation history
- All state must be written to /docs/

---

## Execution model (IMPORTANT)

This workflow is designed to be run in separate sessions:

1. Run Step 1 → restart session
2. Run Step 2 → restart session
3. Run Step 3 → restart session
4. Run Step 4

Do NOT chain all steps in a single long session.

---

## Final Output (assembled manually or by follow-up)

- Spec (/docs/spec.md)
- UX adjustments (/docs/ux.md)
- Implementation (/docs/implementation.md)
- Review findings (/docs/review.md)