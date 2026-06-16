# zodbsync Domain Glossary

## Layer

A named filesystem workdir that stores recorded Zope objects. Each layer has:
- `ident`: string identifier derived from the config filename (e.g. `"10-base"`), or `None` for the custom layer
- `workdir`: writable filesystem path where object representations are stored
- `source` (optional): read-only origin that `layer-update` syncs from into `workdir`

Layers are ordered by priority. Lower list index = higher priority (wins over lower layers). The custom layer is always index 0 (highest priority).

## Custom Layer

The fallback layer with `ident=None` and `workdir=base_dir`. Always present as the topmost layer. Used when no named layer can be determined for an object.

## Named Layer

Any layer loaded from the `layers` config directory. Has a string ident and both `workdir` and `source`.

## `obj.zodbsync_layer`

An attribute stored directly on Zope objects in the ZODB. Records which layer ident the object belongs to. This is the authoritative signal for layer resolution — it takes precedence over the object's current filesystem location.

## Layer Resolution

The algorithm used by `record` and `watch` to determine which layer's workdir an object should be written to. Priority order:

1. `obj.zodbsync_layer` attribute (if set)
2. Where the object already exists on the filesystem (highest-priority layer containing a `__meta__` file)
3. Where the parent object exists on the filesystem
4. Custom layer (ultimate fallback — only reached for root-level objects with no prior recording)

## `__frozen__` Marker

A file placed in a layer's workdir directory for a given object path. Signals that all layers with lower priority than the layer containing the marker should be ignored for that object and all its descendants.

Found in the layer at index N → `fs_pathinfo` restricts to `layers[:N+1]`.

Placed manually. Not placed automatically by any zodbsync command. May be removed by `layer-update` when syncing from source.

## `__deleted__` Marker

A file placed in a layer's workdir to shadow a lower layer's definition of an object, marking it as deleted from the system's perspective.

When an object is deleted from Zope:
- If it exists in only one layer: files are removed, no marker needed.
- If it exists in multiple layers: `__deleted__` is placed in the topmost layer that held the object.

## `zodbsync move`

Command that moves an object's filesystem representation from its current layer to a target layer. Updates `obj.zodbsync_layer` in the ZODB. Removes any `__frozen__` marker left in the source layer. Recursive by default; `--no-recurse` available. Skips descendants whose `obj.zodbsync_layer` differs from the source layer (preserves intentional cross-layer assignments). Custom layer is addressed with an empty string as ident.

## `zodbsync copy`

Command that copies an object's current state to a target layer and resets the source layer's workdir to its last git-committed state. Updates `obj.zodbsync_layer` to the target layer. Does not place a `__frozen__` marker automatically. Useful when a base-layer object needs a permanent customer-specific override in a higher-priority layer.

Intended use: the source layer has unstaged changes. Copy captures the current Zope state in the target layer; the source reset to HEAD creates an immediate content difference, preventing compression. If source is already at HEAD (no unstaged changes), the copy is identical to the source content and compression will remove it on the next record/watch cycle — reverting `obj.zodbsync_layer` to the source layer. In that case, place a `__frozen__` marker manually in the target layer's workdir, or edit the object in Zope before the next record/watch run.
