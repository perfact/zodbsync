Status: ready-for-agent

# `__frozen__` semantics generalisation across named layers

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Generalise `__frozen__` so it works correctly when placed in any named layer's workdir, not only in the custom layer.

Currently `fs_pathinfo` only checks `base_dir` (custom layer workdir) for `__frozen__` markers. When it finds one, it restricts the layers list to `layers[:1]` (custom layer only).

After this change:
- `fs_pathinfo` checks **all** layer workdirs for `__frozen__` at each path component while traversing from root to the target path.
- When `__frozen__` is found in the layer at list index N, restrict to `layers[:N+1]` — all layers at index > N (lower priority) are ignored for that path and its descendants.
- Existing behaviour: `__frozen__` in the custom layer (index 0) → `layers[:1]`. This is the same as before.

No zodbsync command places `__frozen__` automatically. It remains a manual marker.

## Acceptance criteria

- [ ] `fs_pathinfo` scans all layer workdirs for `__frozen__` at each path component (not only `base_dir`).
- [ ] `__frozen__` found in layer at index N restricts to `layers[:N+1]`.
- [ ] All existing tests that rely on `__frozen__` in the custom layer continue to pass unchanged.
- [ ] New test: `__frozen__` placed in a named layer's workdir causes layers below it to be ignored for that path and descendants.
- [ ] New test: `__frozen__` in a named layer does not affect paths outside that subtree.

## Blocked by

None — can start immediately.
