---
name: debug
description: Debug a specific issue systematically using evidence, minimal fixes, and file-based output
---

Debug this issue: $ARGUMENTS

Input:
- Bug report, error, or observed behavior: $ARGUMENTS
- Relevant logs, stack traces, test failures, or screenshots (if available)
- Relevant spec or expected behavior docs (if available)
- Suspected files or directories mentioned by the user (if available)

Write output to:
- /docs/debug.md

Goal:
Identify the most likely root cause using evidence, apply the smallest correct fix, and verify the issue is resolved.

---

## Search Strategy

Inspect files in this order:
1. Files explicitly named in the bug report or user input
2. Files directly on the failing execution path
3. Related test files for the failing behavior
4. Only then expand to nearby dependencies if evidence requires it

Do not scan the full repo unless there is strong evidence the issue cannot be isolated locally.

---

## Task

1. Restate the observed issue clearly
2. Define 2–4 plausible hypotheses
3. Gather evidence from the smallest relevant set of files, logs, tests, and runtime behavior
4. Identify the most likely root cause
5. Apply the minimal fix
6. Validate the fix
7. Summarize what changed and any remaining risk

---

## Required Output Format (/docs/debug.md)

# Debug Report

## Observed Issue
- Clear restatement of the problem
- Expected behavior
- Actual behavior

---

## Reproduction
- Steps to reproduce
- Conditions/environment
- Whether reproduced successfully: YES / NO

---

## Hypotheses
1. ...
2. ...
3. ...

---

## Evidence
For each relevant source of evidence:

- Source: log / stack trace / file / test / runtime check
- Findings:
- Why it matters:

---

## Root Cause
- Most likely cause
- Why this hypothesis was selected over others

---

## Fix Plan
- Smallest change needed
- Files to modify
- Why this fix is sufficient

---

## Code Changes
### File: <path>
- Change summary:
- Concise diff/snippet:

---

## Validation
- Tests run:
- Lint run:
- Manual verification:
- Result: FIXED / PARTIALLY FIXED / NOT FIXED

---

## Remaining Risks
- Edge cases not fully validated
- Follow-up checks recommended

---

## Rules

- Do not guess without evidence
- Prefer the smallest fix that explains the observed behavior
- Do not refactor unrelated code
- Do not expand scope beyond resolving the issue
- If reproduction fails, state that clearly and proceed using available evidence
- If multiple causes are possible, rank them and explain uncertainty
- Use only the provided inputs and inspected files
- Do not rely on prior conversation
- Minimize token usage by inspecting the fewest files necessary

---

## Behavior

- Inspect relevant files before changing code
- Start with files named by the user, failing tests, stack traces, and directly related imports/routes/components
- Use logs, stack traces, and tests as primary evidence when available
- If the issue is caused by missing requirements or spec ambiguity, note that clearly
- If a safe fix cannot be made confidently, stop and explain the blocker

---

After writing:
- Confirm file created: /docs/debug.md
- STOP