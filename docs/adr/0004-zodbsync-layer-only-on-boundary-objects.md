# ADR 0004: `obj.zodbsync_layer` only on layer-boundary objects

## Status

Proposed

## Context

ADR 0001 established `obj.zodbsync_layer` as the authoritative layer signal. The original implementation wrote it to every recorded object — even children that were in the same layer as their parent, where rules 2–3 (FS location) would route them correctly without the attribute. This produced ZODB writes on every record/watch cycle for every object, regardless of need. It also required `zodbsync move` and `zodbsync copy` to use the attribute to detect cross-layer children, creating a dependency on the attribute being present on all objects.

## Decision

`obj.zodbsync_layer` is written only on layer-boundary objects: objects whose resolved target layer differs from their parent's filesystem layer (or root-level objects in a named layer). After each write, `record`, `watch`, and `playback` perform a boundary check — setting the attribute if the object is a boundary, deleting it if the object is in the same layer as its parent. Non-boundary objects rely on FS rules 2–3 for routing; rule 1 is load-bearing only at boundaries.

`zodbsync move` and `zodbsync copy` detect cross-layer children via `fs_pathinfo` rather than via the attribute, since non-boundary children will no longer carry it.

## Alternatives considered

Writing the attribute on every object (the prior approach). This works but produces unnecessary ZODB writes on every record/watch cycle and causes `extedit` to silently wipe the attribute on every external-editor save (a pre-existing bug fixed as a side effect of this change).

## Consequences

- `resolve_target_layer` returns `(target_layer_idx, parent_layer_idx)` so callers can perform the boundary check without a redundant `fs_pathinfo` call.
- `mod_write` no longer accepts a `layer` parameter or sets `obj.zodbsync_layer`; `_playback_path` handles boundary writes/clears after calling `mod_write`.
- `mod_read` no longer includes `zodbsync_layer` in its output dict; `_playback_path` excludes it from the content comparison.
- First record/watch run after deployment clears the attribute from all non-boundary objects in the ZODB (one-time cleanup via normal operation — no migration script needed).
- Tests that assert `obj.zodbsync_layer` is set on every recorded object must be updated to assert it only on boundary objects.
