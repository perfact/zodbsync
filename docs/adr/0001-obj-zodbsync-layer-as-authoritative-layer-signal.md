# ADR 0001: `obj.zodbsync_layer` as authoritative layer signal

## Status

Proposed

## Context

`record` and `watch` previously always wrote objects to the custom layer (the topmost FS workdir). In a multi-layer development workflow — where multiple named layers are checked out simultaneously and changes must land in specific layers for separate PRs — this is wrong. Objects need to be recorded into the layer they belong to.

Two possible signals exist for determining the target layer: (a) the object's current filesystem location, and (b) the `obj.zodbsync_layer` attribute stored on the Zope object in the ZODB.

## Decision

`obj.zodbsync_layer` is the authoritative source for layer assignment, taking priority over the filesystem location. The full resolution order is: attribute → FS location → parent FS location → custom layer fallback.

This means a user can trigger a layer move by setting `obj.zodbsync_layer` directly on the Zope object (e.g. via the management interface), and `watch` will detect the attribute change, write the object to the new layer, and delete it from the old layer — without requiring a CLI command.

## Alternatives considered

Making FS location authoritative and requiring `zodbsync move` (a CLI command) as the only way to move objects between layers. This would mean changes made through the Zope UI have no way to express layer intent, and round-tripping through the CLI is always required.

## Consequences

- `record` and `watch` must compare `obj.zodbsync_layer` against the current FS location on every write and move files when they diverge.
- The `zodbsync move` and `zodbsync copy` commands must update `obj.zodbsync_layer` atomically with their filesystem operations, or `watch` will immediately re-move the object back.
- `__frozen__` markers left in the old layer must be cleaned up when an object is moved away.

## Custom layer ident

`load_layer_config` assigns `ident=""` (empty string) to the custom (fallback) layer. This means `obj.zodbsync_layer` carries two distinct states:

1. Attribute absent — object never recorded or layer unresolved. Rule 1 is skipped; rules 2–4 apply.
2. Attribute set to `""` — object was recorded into the custom layer. Rule 1 fires and routes back to the custom layer.

The `if ident is not None` guard in `resolve_target_layer` distinguishes these cleanly: absent returns `None` via `getattr` default, which skips rule 1; `""` is not `None`, so rule 1 runs and matches the custom layer entry.

Setting `obj.zodbsync_layer = ""` from the Zope management interface is therefore sufficient to trigger a move-to-custom-layer on the next `watch` or `record` cycle, consistent with how moves to named layers work.

See ADR 0003 for the rationale behind `""` over `None` or a sentinel object.
