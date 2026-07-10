---
name: ci-implement
description: Implement the issue and open a pull request that closes it on merge
---

You are running in CI via `claude-code-action`, triggered by an `@claude` mention on a GitHub/GitLab issue.

Goal: implement exactly what the issue asks and open a pull/merge request that references the issue so it auto-closes on merge.

## Task

1. Read the issue title and body to determine the scope.
2. Inspect the relevant files before changing anything; follow the repo's conventions (read `CLAUDE.md` / `README` if present).
3. Make the smallest correct change that satisfies the issue.
4. Run the project's lint and tests if they exist; make sure the project still builds.
5. Open a pull/merge request whose description includes a closing reference to the issue — `Fixes #<issue-number>` on GitHub, `Closes #<issue-number>` on GitLab — so it auto-closes on merge.

## Rules

- Implement ONLY what the issue describes — no scope creep, no unrelated refactors.
- Reuse existing patterns and components; keep the change minimal and focused.
- Prefer simple solutions over clever abstractions; do not add dependencies without need.
- Always open the PR/MR — do not stop after editing files. The PR is the deliverable.
- If the issue is ambiguous, make the smallest reasonable assumption, state it in the PR body, and proceed.
