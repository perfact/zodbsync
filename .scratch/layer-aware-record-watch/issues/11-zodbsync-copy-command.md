Status: done

# `zodbsync copy` command

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

A new `zodbsync copy <zope-path> <layer-ident> [--no-recurse]` command that copies an object's current filesystem state to a higher-priority layer and resets the source layer to its last git-committed state.

Behaviour:
- Copy the object's files from the source layer's workdir to the target layer's workdir.
- Run `git checkout HEAD -- <relative-path>` in the source layer's workdir to reset the source to its last committed state.
- Update `obj.zodbsync_layer` to the target layer ident.
- Recursive by default; `--no-recurse` limits to the named object.
- Does NOT place a `__frozen__` marker automatically.
- Does not perform git commits.

Intended use: the source layer has uncommitted changes. After copy, the source resets to HEAD, creating an immediate content difference between target and source — this prevents the compression pass from collapsing the copy on the next `record`/`watch` cycle. If the source is already at HEAD (no uncommitted changes), the copy is content-identical and compression will collapse it on the next cycle. Document this as a known limitation in the command's help text.

## Acceptance criteria

- [x] `zodbsync copy <path> <layer>` copies files to the target layer's workdir.
- [x] Source layer's workdir is reset to `git checkout HEAD -- <path>` for the copied path.
- [x] `obj.zodbsync_layer` is updated to the target layer ident.
- [x] No `__frozen__` marker is placed automatically.
- [x] Recursive by default; `--no-recurse` limits to the named object.
- [x] No git commits are made.
- [x] Command help text documents the compression window limitation.
- [x] Test: copy an object with uncommitted changes; verify target has the modified content, source has the HEAD content, `obj.zodbsync_layer` is updated.
- [x] Test: recursive copy copies the full subtree.

## Blocked by

`05-layer-divergence-handling.md`
