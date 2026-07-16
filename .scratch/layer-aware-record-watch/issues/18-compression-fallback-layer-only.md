Status: done

# Layer compression scoped to the fallback layer; drop `zodbsync copy`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Two connected changes on the (unmerged) `360057-layer-edit` branch.

### 1. Scope compression to the fallback layer

The compression pass in `fs_write` (`perfact/zodbsync/zodbsync.py`, the `for idx, layer in enumerate(pathinfo["layers"])` loop after the write) currently removes an object's representation from its current layer whenever a lower-priority layer holds identical content, cascading down through multiple layers.

Restrict it so it only ever removes a copy from the **fallback layer** (index 0):

- Perform the removal only when the object's current layer is the fallback layer (`pathinfo["layeridx"] == 0`).
- On the first collapse, the fallback copy sinks into the highest-priority named layer holding identical content, then the pass stops (`break`). A copy that lives in a named layer is never removed.

Rationale: a copy deliberately placed in a named layer (e.g. via `zodbsync move`) must survive `record`/`watch`/`playback` even when a lower layer holds identical content. Only the fallback layer keeps its historical "collapse into the identical base layer" behaviour. See ADR 0005.

### 2. Drop the `zodbsync copy` command

`copy` was designed for a workflow that does not fit practice and is redundant now that named-layer placement is stable. Since the branch is unmerged, remove it entirely:

- Delete `perfact/zodbsync/commands/copy.py`.
- Remove the `Copy` import and its entry in the `commands` list in `perfact/zodbsync/main.py`.
- Delete the `copy` tests in `perfact/zodbsync/tests/test_sync.py`: `test_copy_skips_child_in_different_layer_no_attr`, `test_copy_uncommitted_changes`, `test_copy_recursive`, `test_copy_no_recurse`.

The override workflow the command was meant to serve is captured as future work (`zodbsync stash-and-drop`) in the PRD's Further Notes — not implemented here.

## Acceptance criteria

- [x] Compression removes a copy only when it currently lives in the fallback layer.
- [x] A copy in a named layer identical to a lower layer's content is **not** removed by `record`/`watch`/`playback`.
- [x] Fallback-layer collapse into an identical named layer still works (`test_layer_record_compress_simple` passes unchanged).
- [x] Existing divergence tests still pass (`test_layer_divergence_record`, `_watch`, `_record_back`, `_watch_back`, `_clears_frozen`).
- [x] New test `test_layer_named_not_compressed`: object recorded in a named layer L1 with identical content in a lower layer L2 — the object's files remain in L1's workdir.
- [x] `zodbsync copy` is no longer a registered subcommand; `copy.py` and its tests are gone.
- [x] No reference to `copy`/`Copy` remains in `main.py` or `test_sync.py`.

## Blocked by

None — the compression code (issue 03) and `copy` (removed) already exist on the branch.
