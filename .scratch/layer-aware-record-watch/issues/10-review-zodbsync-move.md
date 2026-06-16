Status: ready-for-human

# HITL Review: `zodbsync move` command

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #09.

Check:
- Command is registered correctly in the CLI runner.
- Layer ident resolution handles empty string → custom layer cleanly.
- Recursive skip of mixed-layer descendants is correct — uses `obj.zodbsync_layer` as the signal, not the current FS location.
- Files are fully removed from the source workdir (no orphaned `__meta__`, source, or `__frozen__` files).
- `obj.zodbsync_layer` update is transactional — no partial state if the command fails mid-subtree.
- No git commits are made.
- Tests cover the mixed-layer skip case.

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] `zodbsync move` is correct for single objects, subtrees, and mixed-layer subtrees.

## Blocked by

`09-zodbsync-move-command.md`
