Status: ready-for-human

# HITL Review: Multi-layer deletion logic

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to review

Review the implementation of issue #07.

Check:
- The layer count check is correct — uses the updated `fs_pathinfo` that respects `__frozen__` semantics.
- "Topmost layer" is correctly identified as the layer with the lowest list index that has a `__meta__` file.
- Single-layer deletion does not leave behind empty directories.
- Existing deletion tests (`test_layer_record_deletion`, `test_layer_recreate_deleted`, etc.) still pass.
- The change does not interfere with `__deleted__` markers placed manually or by other operations.

## Acceptance criteria

- [ ] Code reviewed and approved.
- [ ] Single-layer and multi-layer deletion both work correctly.

## Blocked by

`07-multi-layer-deletion.md`
