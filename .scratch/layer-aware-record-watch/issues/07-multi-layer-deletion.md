Status: ready-for-agent

# Multi-layer deletion logic

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Update deletion handling so that objects existing in only one layer are deleted cleanly (no `__deleted__` marker), and objects present in multiple layers get a `__deleted__` marker in the topmost layer.

Current behaviour: when an object is deleted from Zope and it exists in a lower named layer, a `__deleted__` marker is always placed in the custom layer to shadow it.

New behaviour:
- Count how many layers have a `__meta__` file for the path (using the updated `fs_pathinfo`).
- If exactly one layer holds the object: delete its files from that workdir. No `__deleted__` marker.
- If more than one layer holds the object: place `__deleted__` in the workdir of the topmost (lowest list index) layer that holds the object.

The multi-layer case arises when `zodbsync copy` has been used — the object exists in both a named layer and the base layer.

## Acceptance criteria

- [ ] Deleting an object that exists in only one named layer removes the files without creating `__deleted__`.
- [ ] Deleting an object that exists in the custom layer only removes the files without creating `__deleted__` (single-layer case).
- [ ] Deleting an object that exists in two layers places `__deleted__` in the topmost layer.
- [ ] Existing tests for deletion behaviour pass.
- [ ] New tests cover both the single-layer and multi-layer deletion cases.

## Blocked by

`04-review-layer-aware-write.md`
