Status: ready-for-human

# HITL Review: Layer divergence handling

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #05 before proceeding to `zodbsync move` and `zodbsync copy`.

Check:
- Divergence detection is correct — no false positives when layers already agree.
- File deletion from the old layer is clean: the directory is removed if empty, not just the `__meta__` and source files.
- `__frozen__` cleanup is correct — only the marker in the old layer's directory for this specific path is removed, not markers elsewhere.
- The logic is not duplicated between `record_obj` and `_record_object`.
- No edge case when the object's old layer no longer exists in config (e.g. layer was removed).
- Tests cover both `record` and `watch` paths.

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] Divergence handling is correct in both `record` and `watch`.
- [ ] Ready to proceed to issues #09 and #11 in parallel.

## Blocked by

`05-layer-divergence-handling.md`
