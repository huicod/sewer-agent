# Project Agent Entry Point

This file applies to the whole repository. Platform instructions and the user's current explicit request still take precedence.

## Mandatory governance

- Before substantial work, read `project_management/AGENT_PROJECT_DEVELOPMENT_GOVERNANCE.md`.
- Treat its section 0, “最高优先级开发宪法（P0）”, as this repository's highest-priority internal workflow.
- Work only under `/root/autodl-tmp/server` unless the user explicitly expands the scope.
- The main agent owns planning, integration, verification, risk reporting, and final delivery. Delegate only atomic, independently verifiable tasks, and isolate concurrent writes.
- Use the repository's `grill-me` protocol both for material ambiguity and for gaps in shared project knowledge. Investigate facts first, then ask one highest-information project question at a time about goals, users, domain terms, data, metrics, failure costs, deployment, or acceptance.

## Repository map

- `yolov26/code/`: data preparation, training, evaluation, and visualization code.
- `yolov26/experiments/`: selected experiment evidence and reports.
- `vlm_review/`: VLM/YOLO architecture and literature review.
- `project_management/`: governance, decisions, and project-management records.

## Version-control workflow

- This is a colocated Git + jj repository. Use jj for operations that mutate the working copy or history; use Git mainly for read-only inspection and remote compatibility.
- Before editing, inspect `jj status`, `jj diff`, and relevant existing files. After editing, run proportionate tests and inspect the complete diff.
- Keep one purpose per change. Check `jj op log` before any recovery operation. Never run destructive cleanup, history rewrites, pushes, or releases without explicit authorization.
- Dataset archives, generated datasets/runs, and `*.pt` checkpoints are intentionally excluded from ordinary Git history. Do not override those guards casually.

## Guardrail boundary

- Keep development scoped: no routine whole-repository SHA256 checks or input/model/prompt hashing systems; use asset IDs, versions, relevant tests, and a diff review. Preserve `SHA256SUMS.txt` as historical delivery evidence without regenerating it.
- `stop-that-shit` is installed for Codex CLI, but becomes enforcing only after its hooks are explicitly reviewed and trusted with `/hooks` in a fresh CLI session.
- The Codex IDE extension currently does not load plugins. When working in its sidebar, follow the same scope rules manually; for hook enforcement, run Codex CLI from the IDE's integrated terminal in this repository.

## Workflow-tool precedence

- `grill-me` owns the read-only project-knowledge interview. In this repository it asks one question at a time; after the user confirms alignment, the main agent persists the agreed facts separately.
- Trellis owns the durable task/spec/workspace lifecycle after project initialization. Its CLI is installed and the developer name is confirmed as `huicod`; initialization waits for approval of `project_management/DEVELOPMENT_PLAN.md` and requires a post-init review.
- Superpowers supplies implementation techniques such as TDD, systematic debugging, verification, and code review. It does not replace Trellis task state or this repository's governance.
- `project_management/DEVELOPMENT_PLAN.md` is the sole overall roadmap. Trellis owns active task/journal details after initialization. Implementation has not started; wait for the owner's approval.
- Do not let Trellis and Superpowers run competing planning state machines for the same task. The main agent selects and states one controlling workflow.
- Any Superpowers or Trellis instruction that mutates Git branches, worktrees, staging, commits, or merges is overridden here: use the documented jj change/workspace flow, and require explicit review before mutable Git compatibility operations.
