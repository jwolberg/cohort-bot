---
name: spec
description: Turn a rough feature idea into a structured implementation spec with a file-based handoff
---

Turn this feature request into a structured spec: $ARGUMENTS

Write output to: /docs/spec.md

Goal:
Produce an execution-ready spec that is minimal, explicit, and testable.

Process:
1. Identify the user problem being solved
2. Identify target user, workflow, and success condition
3. Define smallest viable scope
4. Identify constraints and edge cases
5. Produce a structured spec

Required output format for /docs/spec.md:

# Feature Spec

## Feature Request
- Original request: $ARGUMENTS

## Problem Statement
- What problem this solves
- Who experiences it
- Why it matters

## Target User and Workflow
- Primary user
- Current workflow
- Desired workflow

## Success Condition
- What must be true for this feature to be considered successful

## Scope
- In scope
- Out of scope

## Constraints
- Technical constraints
- Product constraints
- Existing pattern constraints

## Edge Cases
- Important edge cases only

## Acceptance Criteria
- Explicit, testable requirements

## Implementation Outline
- Minimal implementation approach
- Likely components / systems involved

## File Impact Guess
- Files likely to be changed or created
- Mark as estimate only

## Validation Plan
- How to verify the feature works

Rules:
- Keep scope minimal
- Avoid future enhancements
- Prefer explicit decisions over ambiguity
- Do not implement
- Do not expand beyond the stated request unless required for correctness

After writing:
- Confirm file created: /docs/spec.md
- STOP