# 004 — hotfix: entrypoint ownership message pollutes the command's stdout

Status: draft
Version: 0.1.0 → 0.1.1 (0.1.0 not yet released; the line goes under `[Unreleased]` → `### Fixed`)
Gate (fix only · no API/format change · no migration or new dependency · ≤ 3 source files · no critical decision): holds

Symptom: CI jobs "Build image (amd64)" and "Build image (arm64)" fail at step "Smoke test the image" (`.github/workflows/ci.yml:71-78`). `uid` captures two lines (`Fixing ownership of /data for 99:100` and `99`), so `test "$uid" = "99"` exits 1. Present on `main` since the entrypoint was added (0.1.0, unreleased).
Repro: `docker build -t ap-discord-bot . && docker run --rm -e PUID=99 -e PGID=100 ap-discord-bot id -u` prints two lines instead of `99`.
Cause: `docker/entrypoint.sh:40` — `echo "Fixing ownership of $DATA_DIR for $PUID:$PGID"` writes to stdout, while every other diagnostic in the script (lines 15, 26, 33, 35) writes to stderr. On a fresh container `/data` is owned by root, so the branch at lines 38-42 always runs and its message is mixed into the wrapped command's stdout (confirmed by: code reading + CI log).
Fix: `docker/entrypoint.sh:40` — append ` >&2` to the echo, so it reads `echo "Fixing ownership of $DATA_DIR for $PUID:$PGID" >&2`. Nothing else in the file changes.
Regression test: `tests/test_entrypoint.py` (new) — `test_entrypoint_messages_go_to_stderr`: reads `docker/entrypoint.sh`, and for every line whose stripped content contains `echo ` asserts that `>&2` appears on that same line. Fails before the fix (line 40), passes after. Portable (no Docker, no root, runs on Windows). The end-to-end check is the existing CI smoke step `ci.yml:76-78`, which must go green on the PR for both arches.

Affected — changed symbols, their direct callers/readers, shared data they write:
| Part | Covered by |
|---|---|
| `docker/entrypoint.sh:40` ownership message | `tests/test_entrypoint.py::test_entrypoint_messages_go_to_stderr` |
| `Dockerfile:37-38,56` (copies the script, runs it as ENTRYPOINT via tini) | CI "Build image" job (build step) |
| `.github/workflows/ci.yml:76-78` smoke test reading the command's stdout | CI "Smoke test the image" on amd64 and arm64 |
| Container logs (message now on stderr; `docker logs` still shows it) | manual: on first start of a fresh container, `docker logs <container>` still shows "Fixing ownership of /data for 99:100" |

Changelog: under `[Unreleased]` → `### Fixed`: "The container's start-up message about fixing data folder ownership no longer mixes into the output of the command it runs."

Decisions for the programmer: none
Follow-ups: none
