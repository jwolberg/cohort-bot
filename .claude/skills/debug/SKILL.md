---
name: debug
description: Reproduce the failure, find the root cause, and push a minimal fix to the open PR
---

You are running in CI via `claude-code-action`, triggered by an `@claude` mention — usually on a pull/merge request whose checks are failing, or an issue describing a bug.

Goal: get the change working by finding the root cause and pushing the smallest fix.

## Task

1. Restate the observed failure (failing checks, error output, or the described bug) and the expected behavior.
2. Form 2–4 plausible hypotheses. Gather evidence from the failing execution path, logs, and tests — inspect the fewest files needed.
3. Identify the most likely root cause and why it beats the alternatives.
4. Apply the minimal fix and push it to the existing PR/MR branch.
5. Verify by re-running the relevant tests and lint where possible.
6. Post a short root-cause explanation as a comment on the PR/MR (or the issue).

## Rules

- Do not guess without evidence; prefer the smallest fix that explains the failure.
- No unrelated refactors and no scope expansion.
- Push the fix to the existing PR/MR branch — do not open a second one.
- If you cannot fix it safely, comment the blocker clearly instead of forcing a change.
