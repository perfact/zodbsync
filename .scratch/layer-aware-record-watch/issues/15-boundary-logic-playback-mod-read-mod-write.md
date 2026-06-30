Status: ready-for-agent

# Boundary logic in `_playback_path`; remove `zodbsync_layer` from `mod_read`/`mod_write`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Three coordinated changes that must land together to avoid spurious playback write triggers:

**1. `mod_read`** — remove `meta["zodbsync_layer"] = getattr(obj, "zodbsync_layer", None)`. The attribute is not stored in `__meta__` and must not participate in the content comparison.

**2. `mod_write`** — remove the `layer` parameter and the `obj.zodbsync_layer = layer` line. The attribute is now managed exclusively by the boundary check, not by `mod_write`. Side effect: `extedit` was calling `mod_write` without `layer`, silently setting `obj.zodbsync_layer = None` on every external-editor save and wiping any user-set layer intent — this bug is fixed automatically.

**3. `_playback_path`** — remove the `fs_data["zodbsync_layer"]` injection (the key is no longer in `srv_data` either, so no spurious `fs_data != srv_data` diff). After calling `mod_write`, apply the same boundary check as `record_obj`/`_record_object`:

```python
target_layer_idx = pathinfo["layeridx"]
path_layer = pathinfo["layers"][target_layer_idx]["ident"]
parent_path = path.rstrip("/").rsplit("/", 1)[0] or "/"
parent_layer_idx = (
    self.fs_pathinfo(parent_path)["layeridx"] if parent_path != path else None
)
# ... boundary check and set/del obj.zodbsync_layer ...
```

The boundary check must run regardless of whether `mod_write` was called (i.e. even when `fs_data == srv_data` and the object content was unchanged).

## Acceptance criteria

- [ ] `mod_read` output dict contains no `zodbsync_layer` key
- [ ] `mod_write` has no `layer` parameter; does not touch `obj.zodbsync_layer`
- [ ] `_playback_path` performs the boundary set/clear after each played-back object
- [ ] Playback of an object in a named layer whose parent is in the fallback layer sets `obj.zodbsync_layer`
- [ ] Playback of an object in the same layer as its parent clears `obj.zodbsync_layer` if previously set
- [ ] `extedit` no longer clears `obj.zodbsync_layer` after saving an object through the external editor
- [ ] All existing playback and layer tests pass

## Blocked by

- Issue 14 (boundary check semantics defined and tested in `record`/`watch` first)
