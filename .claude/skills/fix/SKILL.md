---
name: fix
description: Apply a minimal fix based on a debug report
---

Fix this issue using the debug report: $ARGUMENTS

Input:
- /docs/debug.md (REQUIRED)

Write output to:
- /docs/fix.md

Goal:
Apply the smallest correct fix based on the identified root cause.

---

## Task

1. Read /docs/debug.md
2. Extract:
   - root cause
   - fix plan
   - files to modify
3. Implement ONLY the defined fix
4. Validate the result

---

## Required Output Format (/docs/fix.md)

# Fix Implementation

## Root Cause (from debug.md)
- Summary:

---

## Fix Applied
- What was changed
- Why this resolves the issue

---

## Code Changes
### File: <path>
- Change summary:
- Diff/snippet:

---

## Validation
- Re-tested scenario:
- Result: FIXED / PARTIALLY FIXED / NOT FIXED

---

## Rules

- Do NOT re-diagnose the issue
- Do NOT expand scope
- Do NOT refactor unrelated code
- Follow the fix plan strictly
- If fix plan is unclear or incorrect, STOP and flag it

---

After writing:
- Confirm file created: /docs/fix.md
- STOP