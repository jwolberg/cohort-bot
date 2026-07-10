---
name: ci-plan
description: Post a step-by-step implementation plan for this issue as a comment — no code changes, no PR
---

You are running in CI via `claude-code-action`, triggered by an `@claude` mention on a GitHub/GitLab issue.

Goal: produce a concrete, reviewable implementation plan for the issue and post it as a single comment. Do NOT write code or open a pull/merge request.

## Task

1. Read the issue title and body — that is the work to plan.
2. Inspect the relevant files in the checked-out repo so the plan is grounded in the real codebase (read `CLAUDE.md` / `README` first if present).
3. Post ONE comment on the issue containing:
   - **Summary** — one sentence on the change.
   - **Approach** — the strategy and any key decisions or assumptions.
   - **Steps** — an ordered checkbox list of concrete steps.
   - **Files to touch** — the real paths you expect to create or modify.
   - **Risks** — edge cases, data migrations, or anything that could break.

## Rules

- Plan only what the issue asks; do not expand scope.
- Prefer the smallest change that delivers the user value.
- Reference actual files and paths from this repo — no placeholders.
- Do NOT modify files or open a PR/MR. This step is plan-only; implementation happens later.
