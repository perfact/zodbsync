Status: done

# Layer-aware write infrastructure (`record` and `watch`)

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Make `record` and `watch` write each object into the correct named layer workdir instead of always writing to the custom layer.

The layer resolution algorithm (in priority order):
1. `obj.zodbsync_layer` attribute on the Zope object (if set and matches a known layer ident)
2. The highest-priority layer that already has a `__meta__` file for this path on the filesystem
3. The layer where the parent object's `__meta__` file lives
4. Custom layer — ultimate fallback, reached only for root-level objects with no prior recording

Changes required:
- `fs_write` must accept an explicit target layer (or layer index) and write to that layer's workdir rather than always to the custom layer.
- `record_obj` resolves the target layer before calling `fs_write` and passes it in.
- `watch._record_object` does the same.
- After writing, `obj.zodbsync_layer` is updated to the ident of the layer where the object was actually written (same as today, but now the layer may not be the custom layer).
- A new object created under a parent inherits the parent's layer (rule 3 above).

`test_layer_change_into_top` in `test_sync.py` documents the current wrong behaviour with the comment "this is not what we want in the long run." This test must be inverted: the object must appear in the named layer workdir, not the custom layer, after `record`.

This issue covers **only** the normal case where the resolved target layer matches the current filesystem location. Layer divergence (when `obj.zodbsync_layer` disagrees with where the file currently lives) is handled in issue #05.

## Acceptance criteria

- [x] `fs_write` accepts a target layer parameter and writes to that layer's workdir.
- [x] `record_obj` resolves the target layer using the four-step algorithm and passes it to `fs_write`.
- [x] `watch._record_object` uses the same resolution logic.
- [x] `obj.zodbsync_layer` is updated to reflect the layer where the object was written. (superseded by issue 14's boundary-only scheme — attribute is now set/cleared only at layer boundaries, not unconditionally; see ADR 0004)
- [x] New objects created under a parent are recorded into the parent's layer.
- [x] Root-level objects with no `obj.zodbsync_layer` and no FS presence fall back to the custom layer.
- [x] `test_layer_change_into_top` is inverted: asserts object lands in named layer, not custom layer.
- [x] `load_layer_config` sets `ident=""` (not `None`) for the custom layer (see ADR 0003).
- [x] `set_zodbsync_layer` uses `if layer_ident is not None:` so `""` sets the attribute rather than deleting it.
- [x] Tests using "record → FS rename → record" to simulate named-layer membership are updated to cover rule 2 correctly (`test_layer_record_rule2_fs_presence`); the specific "insert `playback`" mechanic became unnecessary once rule 2 (FS presence) stopped depending on the attribute.
- [ ] `test_layer_record_rule4_fallback_custom` assertion updated from `is None` to `== ""` — **not done, and now incorrect to do**: issue 14's boundary-only scheme means a fallback-layer root is not a boundary, so the attribute is deleted/absent rather than set to `""`. Current test correctly asserts `is None`. Leaving unchecked as a stale instruction rather than checking it against contradicting code.
- [x] `test_layer_record_rule2_fs_presence` setup ensures `obj.zodbsync_layer` is absent before the FS move (either omit the initial record or delete the attribute after).
- [x] All other existing layer tests pass. (41/41 `test_layer_*` tests pass)

## Blocked by

`01-frozen-semantics-generalisation.md`

## Comments

Verified against current code (`zodbsync.py::resolve_target_layer`/`fs_write`/`record_obj`, `commands/watch.py::_record_object`, `helpers.py::load_layer_config`/`set_zodbsync_layer`) and full `test_layer_*` suite (41 passed). One AC left unchecked: it was written before issue 14 (boundary-only `zodbsync_layer`) landed and now contradicts the correct, tested behavior — see note above.
