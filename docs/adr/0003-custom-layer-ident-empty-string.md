# ADR 0003: Custom layer ident is `""` not `None`

## Status

Proposed

## Context

`load_layer_config` originally assigned `ident=None` to the custom (fallback) layer. This created an ambiguity: `obj.zodbsync_layer` returned `None` both when the attribute was absent (object never recorded) and when explicitly set to `None` (object recorded into the custom layer). Rule 1 in `resolve_target_layer` used `if ident is not None` to skip the attribute lookup for `None`, meaning custom-layer objects never benefited from rule 1 stickiness. A move-to-custom-layer could not be triggered by attribute assignment alone — only `zodbsync move` (which moves both the FS files and the attribute atomically) worked.

ADR 0001 documented this as a known limitation and named `""` as the correct fix if stickiness for the custom layer became a requirement.

The requirement arose when aligning behaviour between development systems (single layer, objects in the custom/fallback layer) and deployed systems (all objects in named layers). With `ident=None`, objects in the custom layer had no rule 1 stickiness: a Cut+Paste under a named-layer parent would cause the object to be re-recorded into the named layer via rule 3 rather than staying in the custom layer. Named-layer objects are sticky via rule 1. The asymmetry made the fallback layer a special case that behaved differently from every other layer.

## Decision

The custom layer ident is `""` (empty string). `load_layer_config` sets `{"ident": ""}` for the fallback entry. All other layer logic is unchanged:

- `resolve_target_layer` keeps `if ident is not None` — `""` is not `None`, so rule 1 fires for custom-layer objects; absent attribute (getattr default `None`) still skips rule 1.
- `record_obj` and `watch._record_object`: the `current_layer != path_layer` comparison (`None != ""`) evaluates to `True` on the first record of a new object, so `obj.zodbsync_layer = ""` is set correctly without any guard changes.
- `set_zodbsync_layer(obj, layer_ident)`: guard changed from `if layer_ident:` to `if layer_ident is not None:` so that `""` sets the attribute (sticky custom layer) rather than deleting it. Passing `None` still deletes the attribute (re-derive from FS/parent).
- `layer_init` and `layer_update` both filter with `if layer["ident"]`; `""` is falsy and correctly excluded from those operations.

## Consequences

### FS-move + record reverts; FS-move + playback changes layer

With stickiness active for custom-layer objects, an out-of-band filesystem move followed by `record` now routes the object back to the custom layer (rule 1 wins). To change an object's layer via FS manipulation, `playback` must follow the move — playback sets `obj.zodbsync_layer` to the ident of the layer where the FS file now lives, after which `record` and `watch` use rule 1 to write there.

This is the intended semantics: `record` treats ZODB as authoritative, `playback` treats the FS as authoritative.

### Test changes

Tests that used the pattern "record → FS rename → record again" to transition an object from the custom layer to a named layer must be updated. The second `record` now routes back to the custom layer via rule 1 instead of following the FS via rule 2. Fix: insert `playback` after the FS rename to sync `obj.zodbsync_layer` to the named layer ident, then record/watch work via rule 1.

Tests asserting `getattr(obj, "zodbsync_layer", None) is None` after recording to the custom layer must be updated to `== ""`.

### Attribute-only move to custom layer is now possible

Setting `obj.zodbsync_layer = ""` from the Zope management interface is sufficient to trigger a move-to-custom-layer on the next `watch` or `record` cycle, consistent with how moves to named layers work.

## Alternatives considered

**Sentinel object** (`_UNSET = object()`): distinguishes absent from `None` at the call site. Rejected: requires a module-level sentinel, changes the `getattr` call, and uses `is` comparison inconsistently with the rest of the attribute logic. The `""` approach needs no new objects and no logic changes outside `load_layer_config` and `set_zodbsync_layer`.

**Keep `None` with `hasattr` guard**: check `hasattr(obj, "zodbsync_layer")` before consulting `obj.zodbsync_layer`. Rejected: same asymmetry as today — fallback objects would still not get rule 1 stickiness until they moved to a named layer once, because the attribute would not be set on first record to the custom layer.
