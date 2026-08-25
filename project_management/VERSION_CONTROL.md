# Version-control baseline

Recorded: 2026-08-25
Repository root: `/root/autodl-tmp/server`

## Selected mode

The repository uses a **colocated Git + jj workspace**:

- Git provides ecosystem and future remote compatibility.
- jj is the primary local interface for changes, operation history, and recovery.
- `.git/` and `.jj/` share one working copy.

Installed versions:

- Git `2.55.0`
- jj `0.44.0-af45d57de7163cc5f17f3df178a31577e4951ea3`

The initial baseline has not been finalized: no author identity, remote, or published commit has been chosen. Do not invent an identity or push a baseline until the human owner confirms it.

## Tracked-content boundary

`.gitignore` excludes:

- `yolov26/data_archives/`
- extracted/generated data and Ultralytics run directories
- Python environments and caches
- experiment `train.log` files (local-only for now)
- all `*.pt` model checkpoints

At initialization, jj's default `1 MiB` new-file guard refused the two approximately `15 MiB` `train.log` files. On 2026-08-25, the human owner decided not to version training logs for now, so experiment `train.log` files are explicitly ignored. They remain local evidence and may be moved to a dedicated artifact store later; do not delete them merely because they are ignored.

The root `SHA256SUMS.txt` is the original delivery-integrity record. It will not automatically describe later development changes and must not be silently regenerated.

## Routine checks

Run from the repository root:

```bash
jj status
jj diff
jj log
jj op log
git status --short --branch
```

Before recovery, inspect `jj op log` and identify the exact target operation. Before any remote operation, confirm the remote, bookmark/branch mapping, intended commits, and human authorization.
