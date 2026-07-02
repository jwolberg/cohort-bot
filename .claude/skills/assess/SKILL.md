---
name: assess
description: Assess an existing codebase to determine purpose, architecture, naming conventions, implementation patterns, reusable capabilities, and risks before planning or implementation
---

Assess this existing codebase or subsystem: $ARGUMENTS

Input:
- Existing codebase
- User-provided target area: $ARGUMENTS
- README / CLAUDE.md / AGENTS.md / package files / config files, if present
- Existing docs under /docs, if present
- Existing tests and validation scripts, if present
- Existing deployment/runtime files, if present

Write output to:
- /docs/ASSESS.md

Goal:
Create a durable, evidence-based assessment of what the codebase does, how it is organized, what conventions it follows, what reusable capabilities already exist, and what risks or unknowns should be understood before writing specs, plans, or implementation code.

This skill is for understanding the repo before building. It should prevent duplicate work, bad assumptions, unnecessary rewrites, and implementation plans that conflict with the actual codebase.

---

## When to Use

Use this skill when:
- Entering an unfamiliar codebase
- Returning to an old project and needing orientation
- Preparing to write a spec or build plan
- Determining naming conventions, file structure, and architecture patterns
- Figuring out what already exists before adding a new feature
- Auditing a subsystem before modifying it
- Creating the initial /docs/ASSESS.md for future planning and reconciliation

Do NOT use this skill for:
- Implementing code
- Fixing bugs
- Reviewing completed implementation against a spec
- Creating a feature spec from a product idea
- Reconciling an existing build plan ticket-by-ticket

---

## Search Strategy

Inspect files in this order:

1. Root-level orientation files:
   - README
   - CLAUDE.md
   - AGENTS.md
   - package.json
   - pyproject.toml
   - requirements.txt
   - app.yaml
   - Dockerfile
   - vite.config
   - tsconfig
   - eslint/prettier config
   - other obvious project config

2. Application entry points:
   - Backend app/server files
   - Frontend app/router/main files
   - CLI entry points
   - Worker/task/cron entry points

3. Directory structure:
   - Identify major modules, packages, components, routes, services, models, utilities, and tests

4. User-specified area from $ARGUMENTS:
   - Prioritize this subsystem if the user names one

5. Representative implementation files:
   - Inspect enough files to identify conventions
   - Do not exhaustively read every file unless necessary

6. Tests and validation:
   - Identify test framework and test style
   - Note coverage patterns and gaps

7. Runtime/deployment files:
   - Identify hosting model, environment assumptions, services, workers, cron, queues, or external dependencies

Avoid broad repo-wide scans unless the structure cannot be understood from targeted inspection.

---

## Task

1. Restate the assessment scope from $ARGUMENTS
2. Identify the codebase purpose and product/application model
3. Identify the tech stack and runtime model
4. Map the main architecture and execution flow
5. Identify major directories and their responsibilities
6. Identify naming conventions for files, functions, classes, components, routes, models, and utilities
7. Identify coding style and implementation patterns
8. Identify data models, state management, storage, APIs, and integrations
9. Identify reusable capabilities that should be extended rather than rebuilt
10. Identify testing, validation, and deployment patterns
11. Identify risks, fragile areas, inconsistencies, and unknowns
12. Recommend the next best skill to use

---

## Required Output Format (/docs/ASSESS.md)

# Codebase Assessment

## Scope

- Requested assessment:
- Areas inspected:
- Areas not inspected:
- Assessment status: Complete / Partial

---

## Executive Summary

- What this codebase appears to do
- Overall architecture in 2–4 sentences
- Most important thing to understand before modifying it

---

## Product / Application Purpose

- Primary purpose:
- Primary users or actors:
- Core workflows:
- Important business/domain concepts:

---

## Tech Stack

### Backend
- Language/framework:
- Key libraries:
- Runtime/deployment model:

### Frontend
- Language/framework:
- Styling/UI approach:
- State management:
- Build tooling:

### Data / Storage
- Database/storage:
- Data access patterns:
- Important models/entities:

### External Services
- APIs:
- Auth:
- Payments:
- Messaging/email:
- Other integrations:

---

## Entry Points

List the primary execution entry points.

- File:
  - Purpose:
  - Evidence:
  - Notes:

Examples:
- Backend server entry
- Frontend app root
- API route registration
- Worker entry
- Cron/task handlers
- CLI commands

---

## Architecture Map

Describe the high-level architecture.

### Layers / Modules

- Layer/module:
  - Responsibility:
  - Key files:
  - Depends on:
  - Used by:

### Main Flow

Describe the most important request, user, or data flow.

1. Step:
   - File/function/component:
   - Evidence:

2. Step:
   - File/function/component:
   - Evidence:

---

## Directory and File Roles

Document the important directories and what they contain.

- Path:
  - Role:
  - Naming pattern:
  - Notes:

---

## Naming Conventions

Document observed conventions with examples.

### Files and Directories
- Convention:
- Examples:
- Notes:

### Functions
- Convention:
- Examples:
- Notes:

### Classes / Components
- Convention:
- Examples:
- Notes:

### Routes / API Endpoints
- Convention:
- Examples:
- Notes:

### Models / Data Structures
- Convention:
- Examples:
- Notes:

---

## Coding and Implementation Patterns

Document patterns that future implementation should follow.

- Pattern:
  - Where observed:
  - How to follow it:
  - Risk if ignored:

Examples:
- Error handling
- Logging
- Auth checks
- Data fetching
- API response shape
- Component structure
- Form handling
- Validation
- Caching
- Background jobs
- Environment variables

---

## Data and State Model

### Core Entities

- Entity/model:
  - File:
  - Fields/properties:
  - Purpose:
  - Notes:

### State Flow

- Where state is created:
- Where state is updated:
- Where state is consumed:
- Persistence behavior:

---

## API / Routes / Integrations

### Internal Routes / APIs

- Endpoint/function:
  - File:
  - Purpose:
  - Request shape:
  - Response shape:
  - Auth/permissions:
  - Notes:

### External Integrations

- Service:
  - File(s):
  - Purpose:
  - Failure handling:
  - Notes:

---

## Existing Reusable Capabilities

List functionality that already exists and should be reused, wrapped, or extended instead of rebuilt.

- Capability:
  - File(s):
  - What it does:
  - Reuse guidance:
  - Confidence: High / Medium / Low

---

## Testing and Validation Patterns

- Test framework:
- Test file locations:
- Naming pattern:
- How tests are run:
- Existing coverage patterns:
- Important gaps:
- Manual validation patterns:

---

## Deployment / Runtime Notes

- Hosting/runtime:
- Environment variables:
- Build command:
- Start command:
- Worker/cron/task setup:
- Known operational constraints:

---

## Risks and Fragile Areas

List risks that matter before implementation.

- Risk:
  - Evidence:
  - Why it matters:
  - Mitigation:

Examples:
- Duplicated logic
- Unclear ownership boundaries
- Missing validation
- Weak error handling
- Hidden coupling
- Hardcoded assumptions
- Inconsistent naming
- Untested critical paths

---

## Unknowns / Needs Verification

List anything that could not be confidently determined.

- Unknown:
  - Why it matters:
  - How to verify:

---

## Recommendations

### Recommended Next Skill

Choose one:

- spec
- plan
- implement
- review
- debug
- reconcile

Recommended next skill:
- Skill:
- Why:

### Suggested Next Action

- Next action:
- Rationale:
- Files likely involved:

---

## Evidence Index

List the most important files inspected.

- File:
  - Why it mattered:

---

## Rules

- Do NOT implement code
- Do NOT modify application files
- Do NOT refactor
- Do NOT create a build plan
- Do NOT write a product spec unless explicitly asked
- Base conclusions on inspected files, not assumptions
- Include file-path evidence for every important conclusion
- Be explicit when something is inferred rather than verified
- Prefer concise, useful assessment over exhaustive documentation
- Focus on what future agents need to avoid duplicate work and bad assumptions
- If the codebase is large, assess the most relevant subsystem first and mark the assessment Partial
- If $ARGUMENTS names a subsystem, prioritize that subsystem over global repo exploration

---

## Behavior

- If /docs/ASSESS.md already exists, update it only if explicitly asked; otherwise report that it already exists
- If the repo has no docs, infer cautiously from entry points and config
- If conventions are inconsistent, document the inconsistency rather than inventing a standard
- If architecture is unclear, map what is known and list verification steps
- If a feature request is included in $ARGUMENTS, assess the existing codebase first and recommend whether to use spec, plan, reconcile, debug, or implement next
- If a build plan already exists, recommend reconcile after assessment
- If no spec or build plan exists, recommend spec after assessment

---

After writing:
- Confirm file created: /docs/ASSESS.md
- State recommended next skill
- STOP