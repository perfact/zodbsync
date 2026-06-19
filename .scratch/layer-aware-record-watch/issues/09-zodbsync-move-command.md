Status: ready-for-agent

# `zodbsync move` command

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

A new `zodbsync move <zope-path> <layer-ident> [--no-recurse]` command that moves an object's filesystem representation from its current layer to a target layer and updates `obj.zodbsync_layer`.

Behaviour:
- Resolve `<layer-ident>` to a layer config entry. Empty string resolves to the custom layer (ident=None).
- Move the object's files from the current layer's workdir to the target layer's workdir.
- Delete the files from the source layer's workdir.
- Update `obj.zodbsync_layer` to the target layer ident in a ZODB transaction.
- By default, operate recursively on the entire subtree.
- With `--no-recurse`, move only the named object.
- Recursive move skips descendants whose `obj.zodbsync_layer` already differs from the source layer — these are intentional cross-layer assignments and must not be trampled. To move such descendants, invoke `zodbsync move` on them explicitly.
- Does not perform git commits. The caller is responsible for committing both the old and new layer workdirs.

Because `record`/`watch` now detect layer divergence (issue #05) and move files automatically when `obj.zodbsync_layer` disagrees with the filesystem, `zodbsync move` can be implemented by updating `obj.zodbsync_layer` on the ZODB objects and then calling `record` on the subtree — or by moving the files directly and updating the attribute. Either approach is acceptable; choose whichever is simpler.

## Acceptance criteria

- [ ] `zodbsync move <path> <layer>` moves files to the target layer's workdir and deletes from the source.
- [ ] `obj.zodbsync_layer` is updated on the moved object(s).
- [ ] Empty string ident moves to the custom layer.
- [ ] Recursive by default; `--no-recurse` limits to the named object.
- [ ] Recursive move skips descendants already in a different layer.
- [ ] No git commits are made by the command.
- [ ] Test: move a single object; verify FS state and `obj.zodbsync_layer`.
- [ ] Test: recursive move of a subtree; verify all descendants moved.
- [ ] Test: recursive move skips a child already in a different layer.
- [ ] Test: move to custom layer using empty string ident.

## Blocked by

`05-layer-divergence-handling.md`
