Status: ready-for-agent

# Layer divergence handling in `record` and `watch`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Detect and act on the case where `obj.zodbsync_layer` disagrees with the object's current filesystem location, in both `record` and `watch`.

This happens when a developer sets `obj.zodbsync_layer` directly on a Zope object from the management interface, or after a future `zodbsync move`/`zodbsync copy` call fails partway. The attribute says the object should be in layer A, but the filesystem still has it in layer B.

When divergence is detected (resolved target layer ≠ layer where `__meta__` currently lives on the filesystem):
1. Write the object to the new target layer's workdir.
2. Delete the object's files from the old layer's workdir.
3. If a `__frozen__` marker exists in the old layer's workdir directory for this path, remove it.

This logic must be present in both `record_obj` and `watch._record_object` — a developer may trigger a layer move via the Zope management interface (watch detects it) or by running `zodbsync record` manually.

## Acceptance criteria

- [ ] When `obj.zodbsync_layer` names a layer that differs from the object's current FS location, `record` writes to the new layer and deletes from the old layer.
- [ ] `watch` detects the same divergence and performs the same file operations on the next cycle.
- [ ] A `__frozen__` marker in the old layer's directory is removed as part of the move.
- [ ] `obj.zodbsync_layer` is updated to the new layer ident after the move.
- [ ] Test: set `obj.zodbsync_layer` on a Zope object via the management interface → `watch` moves the file to the new layer's workdir and removes it from the old one.
- [ ] Test: set `obj.zodbsync_layer` then run `zodbsync record` → same result as above.
- [ ] Test: divergence move removes a pre-existing `__frozen__` marker from the old layer.

## Scope limitation: moving to the custom layer via attribute

Divergence detection fires when `obj.zodbsync_layer` holds a **named layer ident** (non-`None`) that differs from the object's current FS location. It does not fire when the attribute is `None`, because `None` is indistinguishable from "attribute not set" (see ADR 0001).

Consequence: a developer cannot trigger a move-to-custom-layer by setting `obj.zodbsync_layer = None` from the Zope UI. Divergence detection would not see it as a move instruction; rule 2 (FS presence in named layer) would win and write back to the named layer unchanged.

Moving an object to the custom layer is therefore only supported via `zodbsync move <path> ""`, which physically moves FS files and sets the attribute atomically. Attribute-only moves to the custom layer are out of scope here.

If attribute-only moves to the custom layer become a requirement in a future issue, the prerequisite is changing the custom layer ident from `None` to `""` throughout `load_layer_config`, the resolution algorithm, and the `__meta__` format (see ADR 0001 for the full scope of that change).

## Blocked by

`04-review-layer-aware-write.md`
