---
name: plan
description: Convert the project's PRD + ARCHITECTURE (with challenge.md/spec.md as authoritative hard requirements) into an execution-ready build plan with phases, tickets, dependencies, and status tracking
---
 
Plan this product strictly from its design docs, using whichever are present.

## Inputs — authority order (higher wins on conflict)


1. **`/docs/PRD.md`** — **PRIMARY DRIVER**: scope, decisions, per-component
   requirements, acceptance, milestones, rubric. (If no PRD, fall back to
   `/docs/spec.md` as the primary.)
2. **`/docs/ARCHITECTURE.md`** — **STRUCTURE / HOW**: module layout, component
   interfaces, data-flow contracts, trust boundary, tech stack. Use if present.
3. **`/docs/ux.md`** — clarification only (OPTIONAL).

**Role split:** PRD answers *what / why / acceptance*; ARCHITECTURE answers
*where / how / interfaces*; challenge.md/spec.md sets the *hard requirements*.
Use only the docs that exist — a project may have one or several.

Write output to:
- **`/docs/BUILD_PLAN.md`**

Goal:
Translate the design docs into a durable, execution-ready build plan that can
survive session resets and guide implementation one phase/ticket at a time.

---

## Task

1. Read the PRD carefully (primary). Read challenge.md/spec.md for hard
   requirements + acceptance; ARCHITECTURE for module layout & interfaces.
2. Incorporate `/docs/ux.md` if present (clarification only, not scope).
3. Extract: core problem, scoped solution, acceptance criteria, non-goals,
   architecture constraints & component boundaries, performance/quality bars.
4. Break the work into sequential phases (reuse PRD milestones if present).
5. Break each phase into concrete tickets mapped to the mandated module layout.
6. Order tickets by dependency.
7. Mark the recommended starting point.
8. Write a durable status-tracking plan.

---

## Required Output Format (/docs/BUILD_PLAN.md)

# Build Plan

## Project
- Name:
- Summary:

## Source of Truth (authority order)
1. /docs/challenge.md (or spec.md) — authoritative hard requirements *(if used)*
2. /docs/PRD.md — scope / decisions / acceptance
3. /docs/ARCHITECTURE.md — structure / interfaces *(if used)*
- Note any doc NOT used and why.

## Planning Assumptions
- Minimal assumptions only
- Any ambiguities / open decisions carried from the docs

## Architecture Notes
- Stack + module layout (from ARCHITECTURE)
- Important cross-cutting constraints (trust boundary, data-flow ordering)
- Explicit non-goals that affect implementation

## Current Status
- Overall status: Not Started
- Current phase:
- Current ticket:
- Blockers: None

---

## Phase Breakdown

### Phase 1 — <name>
**Goal**
- What this phase delivers

**Exit Criteria**
- What must be true for this phase to be complete

**Tickets**
- P1-T1 — <ticket name>
  - Objective:
  - Modules / files (per ARCHITECTURE layout):
  - Depends on:
  - Acceptance criteria (cite PRD §x / challenge.md / benchmark):
  - Commit: one commit on completion, message references this ticket
  - Status: Todo

(repeat per ticket / phase)

---

## Dependency Order
1. P1-T1
2. ...

## Recommended Next Step
- Start with: <ticket id + name>
- Why this is first:

## Deferred / Out of Scope
- Items from non-goals
- Nice-to-haves not needed for MVP

## Update Rules
After each implementation pass:
- Update ticket status only as Todo / In Progress / Complete / Blocked
- Update Current Status and the next recommended ticket
- Record blockers briefly
- One ticket = one git commit (per project CLAUDE.md); log off-spec
  decisions/tradeoffs in `docs/implementation-notes.md`
- Do NOT add new scope unless the docs change

---

## Rules

- Plan from the docs above in the stated authority order; **challenge.md/spec.md
  wins** on any conflict.
- Use `/docs/ux.md` only for clarification.
- Do NOT expand product scope.
- **Cite sections, not whole docs**, in each ticket (e.g. "PRD §7.4 + §11;
  lives per ARCH §3; respects ARCH §5 data-flow rules") for traceability.
- Keep phases incremental and independently testable.
- Keep tickets small and implementation-ready; each maps to one commit.
- Prefer the smallest viable sequence to a working product.
- Do NOT rely on prior conversation.
- Be explicit about dependencies.

---

## Behavior

- If the PRD already includes milestones/phases, refine them into
  execution-ready tickets (don't reinvent).
- If a build plan already exists, update it only if explicitly asked; otherwise
  create from scratch.
- If something is unclear, make minimal assumptions and state them.
- Only the docs that exist are required; adapt to one-doc or multi-doc projects.

---

After writing:
- Confirm file created: /docs/BUILD_PLAN.md
- STOP
