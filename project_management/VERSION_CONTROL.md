# Version-control baseline

Recorded: 2026-08-25; identity updated: 2026-09-09
Repository root: `/root/autodl-tmp/server`

## Selected mode

The repository uses a **colocated Git + jj workspace**:

- Git provides ecosystem and future remote compatibility.
- jj is the primary local interface for changes, operation history, and recovery.
- `.git/` and `.jj/` share one working copy.

Installed versions:

- Git `2.55.0`
- jj `0.44.0-af45d57de7163cc5f17f3df178a31577e4951ea3`

Repository-local Git and jj authors are configured as `huicod <109233438+huicod@users.noreply.github.com>`, as authorized by the owner. The public [GitHub account API](https://api.github.com/users/huicod) provides the account ID; the address follows GitHub's [ID-based noreply format](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference).

Commit authorship and GitHub authentication are separate. The owner has now completed GitHub CLI browser authorization as `huicod`; CLI `2.100.0` uses HTTPS. No account password was requested or read. The CLI reported local plaintext credential storage on this headless server; never print or commit that credential file.

At the owner's request, the private repository [huicod/sewer-agent](https://github.com/huicod/sewer-agent) was created and connected as `origin` using `https://github.com/huicod/sewer-agent.git`. The owner subsequently authorized the initial upload to `main`: existing code, planning documents, and selected experiment evidence; no datasets, model checkpoints, training logs, or credentials.

The imported file contents are preserved. Before the first upload, the unpublished import snapshot's missing author and description were set to `huicod` and `chore: import sewer inspection baseline`. Planning edits remain in a separate child change (`docs: plan inspection agent prototype`). Formal implementation remains pending approval of the development plan.

## Tracked-content boundary

`.gitignore` excludes:

- `yolov26/data_archives/`
- extracted/generated data and Ultralytics run directories
- Python environments and caches
- experiment `train.log` files (local-only for now)
- all `*.pt` model checkpoints

At initialization, jj's default `1 MiB` new-file guard refused the two approximately `15 MiB` `train.log` files. On 2026-08-25, the human owner decided not to version training logs for now, so experiment `train.log` files are explicitly ignored. They remain local evidence and may be moved to a dedicated artifact store later; do not delete them merely because they are ignored.

The root `SHA256SUMS.txt` is the original delivery-integrity record. It will not automatically describe later development changes and must not be regenerated for routine development. It is not a routine validation gate; review the current diff and run only relevant tests.

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
