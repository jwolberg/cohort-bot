---
name: implement
description: Implement a feature or ticket strictly from spec and build plan using minimal, file-driven execution
---

Implement this scope: $ARGUMENTS

Input:
- /docs/spec.md (REQUIRED)
- /docs/BUILD_PLAN.md (REQUIRED)
- /docs/ux.md (OPTIONAL)

Write output to:
- /docs/implementation.md

Goal:
Implement EXACTLY the requested ticket or feature from the spec/build plan with minimal changes, full traceability, and no scope expansion.

---

## Task

1. Read /docs/spec.md carefully
2. Read /docs/BUILD_PLAN.md carefully
3. Incorporate /docs/ux.md if present (ONLY for clarification, not scope expansion)
4. Determine the exact ticket or scope being implemented from $ARGUMENTS
5. Inspect relevant files BEFORE making changes
6. Produce a minimal implementation plan
7. Implement in small, controlled steps
8. Validate implementation
9. Update /docs/BUILD_PLAN.md to reflect the new status

---

## Required Output Format (/docs/implementation.md)

# Implementation

## Scope Implemented
- Requested scope:
- Related phase:
- Related ticket(s):

## Approach
- High-level strategy
- Key decisions
- Assumptions (if any)

---

## Implementation Plan
- Step-by-step plan BEFORE coding
- Files to modify/create

---

## Code Changes
For EACH file:

### File: <path>
- Change summary:
- Code snippet or diff (concise):

---

## Acceptance Criteria Mapping
For EACH relevant acceptance criterion in /docs/spec.md:

- Criterion:
- Implementation:
- File(s):

---

## Build Plan Mapping
For EACH related ticket in /docs/BUILD_PLAN.md:

- Ticket:
- Status:
- What was completed:
- Remaining work (if any):

---

## Validation
- How the feature was tested
- Lint/test results (if available)
- Manual verification steps
- Visible user outcome

---

## Open Issues
- Known limitations
- Unresolved edge cases (if any)
- Blockers (if any)

---

## BUILD_PLAN Update
Update /docs/BUILD_PLAN.md to reflect:
- Current phase
- Current ticket
- Updated ticket status
- Any blockers
- Recommended next ticket

Use only these statuses:
- Todo
- In Progress
- Complete
- Blocked

---

## Rules

- Implement ONLY what is defined in /docs/spec.md and the requested scope in /docs/BUILD_PLAN.md
- Do NOT expand scope
- Do NOT refactor unrelated code
- Reuse existing patterns and components
- Keep changes minimal and focused
- Prefer simple solutions over complex ones
- Be explicit and traceable
- Use only provided inputs and current repo state
- Do NOT rely on prior conversation
- Do NOT mark work complete unless the primary visible behavior works

---

## Behavior

- If $ARGUMENTS names a ticket, implement only that ticket
- If $ARGUMENTS names a feature, map it to the smallest matching ticket(s) in /docs/BUILD_PLAN.md
- If spec is unclear, make minimal assumptions and state them
- If required files are missing, note it and proceed cautiously
- If implementation requires deviation from spec, STOP and flag it
- If tests pass but UI is broken, do NOT mark ticket complete
- If work is partial, mark the ticket In Progress instead of Complete

---

After writing:
- Confirm file created: /docs/implementation.md
- Confirm /docs/BUILD_PLAN.md updated
- STOP