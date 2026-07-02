---
name: reconcile
description: Reconcile the OptionsAce build plan against the actual repo before building anything new
---

Reconcile this phase or subsystem against the existing codebase: $ARGUMENTS

Input:

* /docs/ASSESS.md
* /docs/BUILD_PLAN.md
* Existing OptionsAce codebase
* Relevant tests and config files

Write output to:

* /docs/phase-reconciliation.md

Goal:
Use the actual repo to determine what is already working, what is partial, and what still needs to be built. Do not assume the build plan is correct.

---

## Task

1. Read /docs/ASSESS.md and /docs/BUILD_PLAN.md
2. Inspect the real implementation before making conclusions
3. Focus on the phase, tickets, or subsystem named in $ARGUMENTS
4. For each relevant ticket, classify it as:

   * Complete
   * Partial
   * Missing
   * Mis-scoped
5. Provide file-based evidence for every conclusion
6. Identify existing code that should be reused, wrapped, or extended
7. Identify plan assumptions that conflict with the real architecture
8. Recommend precise edits to the build plan
9. Do NOT implement changes

---

## Required Output Format (/docs/phase-reconciliation.md)

# Phase Reconciliation

## Scope

* Reviewed area
* Source docs used
* Code paths inspected

## Summary

* Overall verdict
* Main findings

## Ticket Review

### [Ticket Name]

* Status: Complete | Partial | Missing | Mis-scoped
* Evidence:

  * file paths
  * functions/classes
* Notes
* Reuse Guidance

## Existing Reusable Capabilities

* Current working broker/trading/system functionality that should be preserved

## True Gaps

* What is genuinely absent

## Build Plan Changes

* Keep
* Modify
* Delete
* Add

## Blockers

* Anything unsafe or unverified that blocks moving forward

## Next Step

* Best next engineering task

---

## Rules

* Do not trust status labels in the plan without code evidence
* Prefer wrapping existing Tradier integration over rebuilding parallel broker logic
* Call out safety-critical discrepancies immediately
* Be explicit when something is inferred vs verified
