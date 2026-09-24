Status: done

# `zodbsync move` — copytree-based FS move and root-only ZODB attr

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Replace the per-object FS copy loop in `move._move_obj` with a single
`shutil.copytree` call at the root, and limit ZODB `zodbsync_layer` writes to
the root object only.

### Recursive case

```
shutil.copytree(
    src_workdir/__root__/<path>/,
    tgt_workdir/__root__/<path>/,
    dirs_exist_ok=True,
)
shutil.rmtree(src_workdir/__root__/<path>/)
```

This copies every file in the source workdir subtree in one call and handles
pre-existing target directories (sub-boundary scaffolding) via `dirs_exist_ok`.
Sub-boundary objects live in OTHER workdirs — the copytree never touches them.

After the FS move, apply exactly two ZODB changes (single transaction):

1. `root.zodbsync_layer = tgt_ident` — marks the new layer boundary.
2. Recurse through ZODB children: for any descendant where
   `getattr(aq_base(child), "zodbsync_layer", None) == src_ident`, delete the
   attribute. These are legacy same-layer attrs (set by the old "every object
   gets an attr" scheme); after the move they would cause `resolve_target_layer`
   Rule 1 to route back to the old layer.

Sub-boundary children (attr ≠ src_ident) are left untouched — their FS is in
their own workdir (not affected by copytree) and their attr still correctly
marks the boundary.

### `--no-recurse` case

Unchanged: copy only `__meta__` / `__source*__` files for the root object, set
`root.zodbsync_layer = tgt_ident`.

## Acceptance criteria

- [x] Recursive move produces correct FS result via copytree (no per-object loop)
- [x] Only root's `zodbsync_layer` is set to `tgt_ident`; non-boundary children carry no attr
- [x] Legacy same-layer `zodbsync_layer = src_ident` on a child is cleared during move
- [x] Sub-boundary children (attr ≠ src_ident) are preserved in their layer, attr unchanged
- [x] `--no-recurse` behaviour is unchanged
- [x] All existing move tests pass (with `test_move_recursive` updated to expect no attr on child)

## Blocked by

- Issue 16 (FS-based descendant skip, now superseded by copytree approach)
