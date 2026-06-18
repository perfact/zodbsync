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

## Custom layer ident and the `None` ambiguity

`load_layer_config` assigns `ident=None` to the custom (fallback) layer. This means `obj.zodbsync_layer` returns `None` in two distinct states:

1. Attribute absent — object never recorded or layer unresolved.
2. Attribute set to `None` — object was recorded into the custom layer.

These two states are intentionally treated identically. The layer resolution algorithm skips rule 1 (attribute lookup) when the value is `None` and falls through to rule 2 (FS presence), which correctly distinguishes them: if the object is on the filesystem in the custom layer, rule 2 routes it there; if it has no FS presence at all, rules 3/4 apply.

**Limitation:** A developer cannot express "move this object to the custom layer" by setting `obj.zodbsync_layer` alone. Setting the attribute to `None` looks identical to "not set", so rule 1 never fires for the custom layer. Moving an object to the custom layer therefore requires `zodbsync move` (which moves both the FS files and sets the attribute), not a bare attribute assignment.

**If this limitation must be lifted** — i.e., if it becomes a requirement to trigger a move-to-custom-layer via attribute assignment from the Zope UI — the correct fix is to change the custom layer ident from `None` to `""` (empty string), matching the `zodbsync move ""` convention. This would require updating `load_layer_config`, all `ident is None` guards, and the `__meta__` serialisation format. That change should be done as a standalone migration, not folded into an individual feature issue.
