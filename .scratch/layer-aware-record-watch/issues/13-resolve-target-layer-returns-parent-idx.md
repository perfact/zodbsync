Status: done

# `resolve_target_layer` returns parent layer index

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Change `resolve_target_layer(path, obj)` from returning a single `int` to returning `(target_layer_idx, parent_layer_idx)`. `parent_layer_idx` is the result of `fs_pathinfo(parent_path)["layeridx"]` — `None` if the parent has no FS presence (root-level objects or objects whose parent is unrecorded). Compute `fs_pathinfo(parent)` unconditionally for every call, not only when rules 1 and 2 fail to fire (as rule 3 currently does).

Update both callers to unpack the tuple:
- `zodbsync.py` — `record_obj`
- `commands/watch.py` — `_record_object`

Both callers currently only use the first value; the second is plumbing for the boundary check introduced in issue 14.

## Acceptance criteria

- [x] `resolve_target_layer` returns `(target_layer_idx, parent_layer_idx)` in all code paths (rules 1–4)
- [x] `parent_layer_idx` is `None` when the parent path has no `__meta__` file in any layer
- [x] Both callers unpack correctly; behaviour of `record` and `watch` is unchanged
- [x] All existing layer tests pass

## Blocked by

None — can start immediately.
