Status: ready-for-human

# HITL Review: Layer-aware write infrastructure

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #03 before proceeding to divergence handling, multi-layer deletion, and the move/copy commands.

Check:
- The four-step layer resolution algorithm is implemented correctly and in the right priority order.
- `fs_write` signature change is clean; no callers accidentally pass the wrong layer.
- `record_obj` and `watch._record_object` share the resolution logic (not duplicated).
- Parent-layer inheritance works correctly for deeply nested new objects.
- Custom-layer fallback is only reached when genuinely no other resolution is possible.
- `test_layer_change_into_top` inversion is correct — the test now asserts the new desired behaviour.
- `obj.zodbsync_layer` is updated atomically with the write (same transaction discipline as before).

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] Layer resolution logic is correct, shared, and not duplicated between `record` and `watch`.
- [ ] Ready to proceed to issues #05 and #07 in parallel.

## Blocked by

`03-layer-aware-write-infrastructure.md`
