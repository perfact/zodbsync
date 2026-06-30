Status: ready-for-agent

# `zodbsync move` and `copy` — FS-based descendant skip detection

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

`move._move_obj` and `copy._copy_obj` currently skip descendants by reading `obj.zodbsync_layer` off the child object. Under the boundary-only attribute scheme (issue 14), non-boundary children no longer carry the attribute, so the existing check silently fails to skip cross-layer children that have no attribute set.

Replace the attribute-based skip check with an FS-based check using `fs_pathinfo`:

```python
# current (both _move_obj and _copy_obj):
child_ident = getattr(obj_base, "zodbsync_layer", None)
if child_ident is not None and child_ident != src_ident:
    return

# replacement:
child_info = self.sync.fs_pathinfo(path)
if child_info["layeridx"] is not None:
    child_ident = child_info["layers"][child_info["layeridx"]]["ident"]
    if child_ident != src_ident:
        return
```

Children with no FS presence (not yet recorded) have `child_info["layeridx"] = None` — the skip does not fire, and they are processed normally. This is the same behaviour as before for unrecorded children.

## Acceptance criteria

- [ ] `move._move_obj` uses `fs_pathinfo` to detect cross-layer children
- [ ] `copy._copy_obj` uses `fs_pathinfo` to detect cross-layer children
- [ ] Recursive `move` skips a child whose `__meta__` file is in a different layer from the source layer
- [ ] Recursive `move` processes a child with no FS presence (not yet recorded)
- [ ] Recursive `move` processes a child whose `__meta__` is in the same layer as source, regardless of whether `obj.zodbsync_layer` is set on it
- [ ] All existing `move` and `copy` tests pass

## Blocked by

None — can start immediately.
