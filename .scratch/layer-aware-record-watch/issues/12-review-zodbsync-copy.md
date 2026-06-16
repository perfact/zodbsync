Status: ready-for-human

# HITL Review: `zodbsync copy` command

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #11.

Check:
- `git checkout HEAD -- <path>` is scoped to the exact path (not a full workdir reset).
- Handles the case where the path does not yet exist in the source layer's git history (newly created object — `git checkout HEAD` would fail). Decide: skip the reset, or error?
- `obj.zodbsync_layer` update is correct and transactional.
- No `__frozen__` marker is placed by the command.
- The compression window limitation is documented in the help text.
- Tests verify the source-reset behaviour, not just the target copy.

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] Edge case of path not in git history is handled gracefully.
- [ ] `zodbsync copy` is correct for single objects and subtrees.

## Blocked by

`11-zodbsync-copy-command.md`
