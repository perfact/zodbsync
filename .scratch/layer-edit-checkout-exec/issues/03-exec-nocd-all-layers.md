# exec --nocd: check all layers

Status: done

## What to build

Extend `zodbsync exec --nocd` so that when no `--layer` is given, it snapshots all layer HEADs before running the command, stashes unstaged changes in every layer, diffs all layers after the command, accumulates the union of changed paths, and plays them back in one operation. On failure, all layers are reset to their original commits and stashes are popped; a best-effort playback with `skip_errors=True` is attempted for any objects already written to Zope.

This is a multi-layer transaction held under the existing lock. The implementation should bypass `@gitexec` and use explicit multi-layer logic (same approach as `zodbsync reset`). When `--layer` is also given, `--nocd` keeps its current scoped behavior (no cd, check only that layer).

## Acceptance criteria

- [x] `zodbsync exec --nocd "cmd"` (no `--layer`) stashes unstaged changes in all layers, runs `cmd`, diffs all layers, plays back union of changed paths
- [x] All layers are rolled back on failure; best-effort playback attempted for partially applied objects
- [x] Stashes are popped on both success and failure
- [x] `zodbsync exec --nocd --layer "ident" "cmd"` checks only the specified layer (existing scoped behavior, unchanged)
- [x] `zodbsync exec --nocd "cmd"` with no changed paths in any layer is a no-op (no playback)
- [x] Existing `--nocd` behavior (no `--layer`) was previously identical to the fallback-only path — confirm no regression for callers that expected no cross-layer side effects

## Blocked by

- `.scratch/layer-edit-checkout-exec/issues/02-exec-layer-flag.md`

## Comments

Implemented in `perfact/zodbsync/commands/execute.py`: `run()` dispatches to `_run_all_layers()` (decorated `@with_lock`, bypasses `@gitexec`) when `--nocd` and no `--layer`. Snapshots HEAD + unstaged changes for all layers, stashes dirty ones, runs cmd, diffs all layers, plays back union of changed paths. Rolls back all layers + pops stashes on failure; best-effort playback for partially applied objects. Tests: `test_exec_nocd_all_layers`, `test_exec_nocd_no_changes`, `test_exec_nocd_stash`, `test_exec_nocd_failure`.
