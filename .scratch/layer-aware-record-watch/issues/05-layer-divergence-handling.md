Status: done

# Layer divergence handling in `record` and `watch`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Detect and act on the case where `obj.zodbsync_layer` disagrees with the object's current filesystem location, in both `record` and `watch`.

This happens when a developer sets `obj.zodbsync_layer` directly on a Zope object from the management interface, or after a `zodbsync move` call fails partway. The attribute says the object should be in layer A, but the filesystem still has it in layer B.

When divergence is detected (resolved target layer ≠ layer where `__meta__` currently lives on the filesystem):
1. Write the object to the new target layer's workdir.
2. Delete the object's files from the old layer's workdir.
3. If a `__frozen__` marker exists in the old layer's workdir directory for this path, remove it.

This logic must be present in both `record_obj` and `watch._record_object` — a developer may trigger a layer move via the Zope management interface (watch detects it) or by running `zodbsync record` manually.

## Acceptance criteria

- [x] When `obj.zodbsync_layer` names a layer that differs from the object's current FS location, `record` writes to the new layer and deletes from the old layer.
- [x] `watch` detects the same divergence and performs the same file operations on the next cycle.
- [x] A `__frozen__` marker in the old layer's directory is removed as part of the move.
- [x] `obj.zodbsync_layer` is updated to the new layer ident after the move.
- [x] Test: set `obj.zodbsync_layer` on a Zope object via the management interface → `watch` moves the file to the new layer's workdir and removes it from the old one.
- [x] Test: set `obj.zodbsync_layer` then run `zodbsync record` → same result as above.
- [x] Test: divergence move removes a pre-existing `__frozen__` marker from the old layer.
- [x] Divergence detection works in both directions: lower-priority → higher-priority layer (e.g. named → fallback) as well as higher → lower. The old implementation only deleted the old copy when `old_idx < target_idx`, so moving back to a higher-priority layer was silently undone by the compress step. Fixed by deleting the old copy before `fs_write` so compress cannot restore it. Tests: `test_layer_divergence_record_back`, `test_layer_divergence_watch_back`.

## Moving to the fallback layer via attribute

Divergence detection fires whenever `obj.zodbsync_layer` holds any ident (including `""` for the fallback layer) that differs from the object's current FS location. Setting `obj.zodbsync_layer = ""` from the Zope management interface is therefore sufficient to trigger a move-to-fallback-layer on the next `watch` or `record` cycle — the same mechanism as moves to named layers (see ADR 0003).

The attribute must be set (not absent). An absent attribute (`getattr` returns `None`) still skips rule 1 and falls through to rules 2–4.

## Blocked by

`03-layer-aware-write-infrastructure.md`
