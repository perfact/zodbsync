Status: done

# Boundary-only `obj.zodbsync_layer` in `record` and `watch`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Replace the "always write `obj.zodbsync_layer`" block in `record_obj` and `watch._record_object` with set-at-boundary / clear-if-redundant logic. An object is a layer boundary when its target layer differs from its parent's FS layer. Non-boundary objects have their attribute deleted if present.

The boundary condition (using the `parent_layer_idx` now returned by `resolve_target_layer`):

```python
at_boundary = (parent_layer_idx is None and target_layer_idx != 0) or \
              (parent_layer_idx is not None and target_layer_idx != parent_layer_idx)

current_attr = getattr(aq_base(obj), "zodbsync_layer", None)
if at_boundary:
    if current_attr != path_layer:
        obj.zodbsync_layer = path_layer   # set or update
else:
    if current_attr is not None:
        del obj.zodbsync_layer            # clear — FS rules 2–3 cover routing
```

Both the set and the delete must happen inside `self.tm` / `self.sync.tm`.

After this change, existing objects that are not at a layer boundary will have their `zodbsync_layer` attribute deleted the first time they are recorded or watched. This is intentional and requires no migration script.

See ADR 0004 for rationale.

## Acceptance criteria

- [x] `record_obj` applies the boundary check instead of unconditional write
- [x] `watch._record_object` applies the same boundary check
- [x] Objects at a layer boundary carry `obj.zodbsync_layer`; non-boundary objects do not
- [x] Root-level objects in the fallback layer do not carry the attribute
- [x] Root-level objects in a named layer carry the attribute
- [x] Existing tests updated: assertions on `obj.zodbsync_layer` presence/absence reflect boundary semantics
- [x] `test_layer_change_into_top` and related divergence tests pass with updated expectations
- [x] All other layer tests pass

## Blocked by

- Issue 13 (`resolve_target_layer` returns parent layer index)
