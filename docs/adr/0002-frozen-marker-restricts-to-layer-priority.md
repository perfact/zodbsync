# ADR 0002: `__frozen__` restricts to layers at equal or higher priority

## Status

Proposed

## Context

The `__frozen__` marker previously caused `fs_pathinfo` to restrict layer consideration to `layers[:1]` — the custom layer only. This was appropriate when the custom layer was the only writable layer and `__frozen__` meant "this object is customized and should not be overridden by lower layers."

With named writable layers, `__frozen__` placed in a non-custom layer must restrict to that layer and everything above it, not to the custom layer alone.

Additionally, `fs_pathinfo` previously only scanned `base_dir` (custom layer workdir) for `__frozen__` markers. It must now scan all layers' workdirs.

## Decision

When `fs_pathinfo` finds a `__frozen__` marker in a layer at index N, it restricts `layers` to `layers[:N+1]`. All layers at index > N (lower priority) are ignored for that object and all descendants.

The old behavior (`layers[:1]` when found in custom layer at idx=0) is a special case of this rule and requires no migration.

`fs_pathinfo` scans all layers' workdirs for `__frozen__` at each path component, taking the highest-priority (lowest index) layer where the marker is found.

## Alternatives considered

Keeping `__frozen__` as a custom-layer-only concept and introducing a new marker (e.g. `__layer_pin__`) for named layers. Rejected: adds a redundant concept; the generalized semantics subsume the old behavior cleanly.

## Consequences

- `fs_pathinfo` performance: O(layers × path_depth) filesystem checks for `__frozen__`, up from O(path_depth). In practice this is expected to be negligible: `__frozen__` is the exception, so most checks are ENOENT hits served from the kernel dentry cache.
- `layer-update` may remove `__frozen__` markers when syncing source → workdir. This is intentional: `__frozen__` is local workdir state not owned by the source.
- `zodbsync move` removes `__frozen__` from the source layer when an object moves away, preventing stale markers.

### Future optimization

If profiling shows the stat overhead is measurable, a per-layer index file (e.g. `__frozen_index__`) listing all frozen paths could replace per-path stat checks, reducing runtime cost to a single small-file read per session or watch cycle. This would require a one-time migration to build the index from existing `__frozen__` marker files and is deferred until there is evidence it is needed.
