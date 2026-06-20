Status: ready-for-agent

# PRD: Layer-aware record/watch, zodbsync move/copy, layer-scoped pick/reset

## Problem Statement

When working across multiple named layers simultaneously — for example to prepare independent pull requests for separate features — `zodbsync record` and `zodbsync watch` always write changed objects into the custom layer (the topmost fallback workdir), regardless of which named layer the object belongs to. This makes it impossible to keep changes separated by layer. There is also no command to deliberately move an object from one layer to another, or to promote an object from a base layer into a higher-priority override layer.

Additionally, `zodbsync pick` and `zodbsync reset` are hardcoded to operate on the custom layer's git repo (`base_dir`). Attempting to cherry-pick commits from a named layer's history into the custom layer (or vice versa) produces conflicts or wrong results because the two repos have independent histories. There is no way to reset multiple named layers atomically to known states in a single playback operation.

## Solution

`record` and `watch` determine the correct target layer for each object before writing, using the object's `zodbsync_layer` attribute as the authoritative signal. A new `zodbsync move` command moves an object's filesystem representation between layers and updates the attribute. A new `zodbsync copy` command copies an object's current state to a higher-priority layer (for permanent customer-specific overrides) while resetting the source layer to its last committed state. The `__frozen__` marker is generalised so it works across named layers, not just the custom layer.

`zodbsync pick` gains a `--layer <ident>` flag that routes the cherry-pick to the named layer's own git repo. `zodbsync reset` gains a `<ident>:<targetref>` positional syntax that allows resetting one or more named layers in a single atomic operation — git-resetting each, collecting the union of changed paths across all reset repos, and playing them back in one pass.

## User Stories

1. As a developer, I want `zodbsync record` to write a changed object into the named layer where it lives, so that my changes land in the right layer without manual file moves.
2. As a developer, I want `zodbsync watch` to write changed objects into their correct named layer, so that continuous recording keeps changes separated by layer.
3. As a developer, I want to set `obj.zodbsync_layer` directly on a Zope object from the management interface, so that `watch` automatically moves the object's filesystem representation to the new layer on the next cycle.
4. As a developer, I want `record` to detect when `obj.zodbsync_layer` disagrees with the object's current filesystem location and move the files automatically, so that setting the attribute from outside `watch` still produces a correct result.
5. As a developer, I want `zodbsync move <path> <layer>` to move an object's files from its current layer to a target layer and update `obj.zodbsync_layer`, so that I can explicitly reassign objects without manually moving files.
6. As a developer, I want `zodbsync move` to operate recursively by default, so that moving a folder moves its entire subtree without extra arguments.
7. As a developer, I want `zodbsync move --no-recurse` to move only the named object, so that I can move a parent without disturbing children that are intentionally in different layers.
8. As a developer, I want recursive `zodbsync move` to skip descendants whose `obj.zodbsync_layer` differs from the source layer, so that intentional cross-layer child assignments are preserved.
9. As a developer, I want to move an object to the custom layer by passing an empty string as the layer ident, so that I can return objects to the fallback layer without special syntax.
10. As a developer, I want `zodbsync copy <path> <layer>` to copy an object's current state to a target layer and reset the source layer's workdir to its last git-committed state, so that I can create a permanent override in a higher-priority layer while keeping the base layer clean.
11. As a developer, I want `zodbsync copy` to update `obj.zodbsync_layer` to the target layer, so that subsequent `record`/`watch` cycles target the new layer.
12. As a developer, I want `zodbsync copy` to work recursively by default with a `--no-recurse` option, consistent with `zodbsync move`.
13. As a developer, I want a new object created in Zope under a parent to be recorded into the same layer as the parent, so that I do not have to manually assign the layer for every new child object.
14. As a developer, I want root-level objects with no `obj.zodbsync_layer` and no parent layer to fall back to the custom layer, so that recording still works in a single-layer setup without any configuration.
15. As a developer, I want `record` and `watch` to clean up the old filesystem location when moving an object to a new layer, so that the workdir does not accumulate stale copies across layers.
16. As a developer, I want `record` and `watch` to remove any `__frozen__` marker left in the source layer when an object moves away, so that stale markers do not accumulate.
17. As a developer, I want deletion of an object that exists in only one layer to simply remove the files from that layer, without creating a `__deleted__` marker, so that the workdir stays clean.
18. As a developer, I want deletion of an object that exists in multiple layers to place a `__deleted__` marker in the topmost layer that held the object, so that lower-layer definitions are correctly shadowed.
19. As a developer, I want `__frozen__` placed in a named layer's workdir to cause `fs_pathinfo` to ignore all layers below that named layer, so that lower-layer versions are permanently masked for that object and its descendants.
20. As a developer, I want the existing `__frozen__`-in-custom-layer behaviour to remain unchanged, so that existing workdirs do not need migration.
21. As a developer, I want `zodbsync move` to not commit git changes itself, so that I control the commit message and scope across both old and new layer workdirs.
22. As a developer, I want `zodbsync copy` to not commit git changes itself, for the same reason.

23. As a developer, I want `zodbsync pick --layer <ident> <commits>` to cherry-pick those commits in the named layer's own git repo and play back the changed objects, so that I can apply layer-specific commits without confusion with other layers' histories.
24. As a developer, I want `zodbsync pick <commits>` (no `--layer`) to continue operating on the custom layer exactly as before, so that existing usage is not broken.
25. As a developer, I want `pick --layer <ident>` to fail immediately if the named layer's workdir has unstaged changes, so that I cannot accidentally cherry-pick into a dirty state.
26. As a developer, I want `zodbsync reset <ident1>:<commit1> [<ident2>:<commit2> ...]` to reset each named layer's repo to its target commit and play back the union of all changed paths in one operation, so that I can atomically snap multiple layers to known states.
27. As a developer, I want `zodbsync reset <commit>` (bare, no `:`) to continue resetting the custom layer exactly as before, so that existing usage is not broken.
28. As a developer, I want multi-layer `reset` to stash and restore unstaged changes in each affected layer's workdir, so that local work is preserved across the reset.
29. As a developer, I want multi-layer `reset` to roll back ALL layers to their original commits if any step fails (git reset or playback), so that the system is never left in a partial reset state.

## Implementation Decisions

### Layer resolution algorithm

Before writing, `record_obj` and `watch._record_object` resolve the target layer using this priority order:

1. `obj.zodbsync_layer` attribute (if set and valid)
2. The highest-priority layer that already has a `__meta__` file for this path on the filesystem
3. The layer where the parent object's `__meta__` file lives
4. Custom layer (ultimate fallback — only reached for root objects with no prior recording)

### Detecting and handling layer divergence

After resolving the target layer, if it differs from the object's current filesystem location, `record_obj`/`_record_object` must:
1. Write to the new target layer's workdir
2. Delete the object's files from the old layer's workdir
3. Remove any `__frozen__` marker in the old layer's directory for this path

This logic is shared between `record` and `watch`.

### `fs_pathinfo` changes

`fs_pathinfo` scans all layer workdirs (not only `base_dir`) for `__frozen__` and `__deleted__` markers at each path component. When `__frozen__` is found in the layer at list index N, the layers list is restricted to `layers[:N+1]`. The existing `layers[:1]` behaviour for `__frozen__` in the custom layer (index 0) is a special case of this rule.

### `fs_write` changes

`fs_write` must accept an explicit target layer (or layer index) so it writes to the correct workdir rather than always to the custom layer.

### `zodbsync move` command

```
zodbsync move <zope-path> <layer-ident> [--no-recurse]
```

- Resolves `<layer-ident>` to a layer config entry; empty string resolves to the custom layer.
- Moves filesystem files from the object's current layer workdir to the target layer workdir.
- Deletes files from the old layer workdir.
- Updates `obj.zodbsync_layer` in a ZODB transaction.
- Recursively processes descendants by default; skips descendants whose `obj.zodbsync_layer` differs from the source layer.
- Does not perform git commits.

### `zodbsync copy` command

```
zodbsync copy <zope-path> <layer-ident> [--no-recurse]
```

- Copies the current filesystem representation to the target layer workdir.
- Runs `git checkout HEAD -- <path>` in the source layer workdir to reset the source to its last committed state.
- Updates `obj.zodbsync_layer` to the target layer.
- Does NOT place a `__frozen__` marker automatically.
- Does not perform git commits.
- Intended use: the source layer has uncommitted changes. After copy, source resets to HEAD creating an immediate content difference, which prevents compression from collapsing the copy. If the source is already at HEAD, the copy is content-identical and compression will collapse it on the next record/watch cycle — see Known Limitations.

### `__frozen__` marker placement

`__frozen__` is a manual tool. No zodbsync command places it automatically. Users may place it to pin an object's layer assignment and prevent lower layers from shadowing it.

### Deletion logic

When `fs_prune` or watch processes a deletion:
- Count how many layers have a `__meta__` file for the path.
- If one layer: delete the directory from that workdir.
- If more than one layer: place `__deleted__` in the workdir of the topmost (lowest index) layer that held the object.

### Layer-scoped `pick`

```
zodbsync pick [--layer <ident>] [--skip-errors] [--dry-run] [--grep ...] [--since ...] [--until ...] [commit ...]
```

- `--layer <ident>` resolves the ident to a layer config entry (empty string = custom layer).
- When `--layer` is absent or `ident=""`, behaviour is identical to current: operates on `base_dir`.
- When a named layer is specified, all `gitcmd` calls are routed to that layer's `workdir` instead of `base_dir`.
- Before starting, the named layer's workdir is checked for unstaged changes. If any exist, `pick` exits with an error — no stash/restore for named layers.
- After cherry-pick, changed paths are collected by diffing `orig_commit..HEAD` in the named layer's repo. Paths are resolved relative to that layer's workdir before playback.
- Abort logic resets the named layer's repo to its original commit.

### Layer-aware `gitcmd` infrastructure

`SubCommand.gitcmd` (and its siblings `gitcmd_run`, `gitcmd_try`, `gitcmd_output`) must be made layer-aware. Implementation approach: introduce `self._git_workdir` (defaults to `self.config["base_dir"]`). Layer-scoped commands set this before calling the `gitexec`-decorated method. All `gitcmd` variants read from `self._git_workdir`.

`check_repo` and `abort` operate on `self._git_workdir`. For named-layer `pick`, `self._git_workdir` is set to the named layer's `workdir`; for multi-layer `reset` it iterates over all target layers.

### Multi-layer `reset`

```
zodbsync reset <ident>:<targetref> [<ident>:<targetref> ...]   # new multi-target form
zodbsync reset <commit>                                         # existing form — custom layer
```

Parsing: if an argument contains `:`, split on the first `:` to extract `(ident, targetref)`. A bare argument (no `:`) is treated as `("", argument)` — custom layer, backward-compatible.

Execution order (all-or-nothing):

1. For each target layer, record `orig_commit` (current HEAD of that layer's repo) and stash any unstaged changes.
2. For each target layer in order, run `git reset --hard <targetref>` in that layer's `workdir`.
3. For each target layer, diff `orig_commit..HEAD` to collect changed paths. Accumulate the union across all layers.
4. Run `_playback_paths` once on the union.
5. On any failure at steps 2–4: `git reset --hard <orig_commit>` in every target layer that was already reset, then restore all stashes. Re-raise the exception.
6. On success: pop stashes for all layers that had unstaged changes.

Conflict detection (`unstaged_changes & files`) is performed per layer with that layer's own set of unstaged files.

### Modules to test (additions)

- `pick --layer <ident>` — cherry-picks in named layer's repo; objects from that layer played back; custom layer unchanged.
- `pick` with no `--layer` — unchanged behavior on `base_dir`.
- `pick --layer <ident>` with dirty named-layer workdir — fails before cherry-pick.
- `reset <ident>:<commit>` single named layer — repo reset, objects played back, custom layer unchanged.
- `reset <ident1>:<commit1> <ident2>:<commit2>` — both repos reset, union of changed paths played back atomically.
- `reset <commit>` bare — unchanged behavior on `base_dir`.
- Multi-layer `reset` failure mid-way — all layers rolled back, no partial state.

## Testing Decisions

### What makes a good test

Tests assert on filesystem state (presence/absence of `__meta__`, `__deleted__`, `__frozen__`, source files in specific layer workdirs) and on ZODB object state (`obj.zodbsync_layer`). They do not assert on internal method calls or the order of operations inside `fs_write`. Tests use `self.run(...)` to drive the full command pipeline — the same seam used by all existing layer tests.

### Modules to test

- `record` command — layer resolution for new and existing objects, divergence detection, clean-up of old layer, `obj.zodbsync_layer` update
- `watch` command — same as record, triggered via ZODB transaction
- `zodbsync move` — filesystem state before/after, `obj.zodbsync_layer`, recursion, skip of mixed-layer children, empty-string ident for custom layer
- `zodbsync copy` — filesystem state before/after, source-layer reset to HEAD, `obj.zodbsync_layer`, recursion
- `fs_pathinfo` — `__frozen__` found in named layer restricts layers correctly; existing custom-layer `__frozen__` behaviour unchanged
- Deletion — single-layer deletion removes files; multi-layer deletion places `__deleted__` in topmost layer

### Prior art

All new tests should follow the pattern of `test_layer_record`, `test_layer_watch_rename`, and `test_layer_change_into_top` in `perfact/zodbsync/tests/test_sync.py`, using `self.addlayer()`, `self.runner.sync.tm`, and `self.run(...)`.

`test_layer_change_into_top` (line 2207) explicitly documents the current wrong behaviour with the comment "this is not what we want in the long run." This test must be inverted: after the feature, the object must appear in the named layer workdir, not in the custom layer.

The `pick` and `reset` additions are tested following the same pattern as existing layer tests (`self.addlayer()`, `self.run(...)`, assert on filesystem state and ZODB object state).

## Out of Scope

- Git commits inside `zodbsync move` or `zodbsync copy` — the caller is responsible.
- Any UI for displaying uncommitted changes per layer.
- Automatic `__frozen__` placement by any zodbsync command.
- `layer-update` preserving `__frozen__` markers — `layer-update` may overwrite them; this is intentional.
- Handling the case where `obj.zodbsync_layer` references a layer ident that no longer exists in config.
- Moving objects to a lower-priority layer when a higher-priority layer has a source that `layer-update` would restore — this is a known operational risk, not addressed here.
- Cross-layer `pick` (cherry-picking a commit from one layer's history into a different layer) — layers have independent git histories; this is intentionally not supported.
- `pick --layer` with multiple layers in one invocation — call `pick --layer` twice instead.
- Stash/restore for named-layer `pick` — named layer workdirs must be clean before picking.

## Further Notes

### Known limitation: copy + compression window

If `zodbsync copy` is used on an object whose source layer is already at its last committed state (no uncommitted changes), the copied content is identical to the source layer's content. On the next `record`/`watch` cycle, the compression pass will remove the copy from the target layer and revert `obj.zodbsync_layer` to the source layer. To avoid this, either edit the object in Zope before the next record/watch run, or manually place a `__frozen__` marker in the target layer's workdir directory for that path.

The intended use of `zodbsync copy` is to promote an object that already has uncommitted changes in the source layer. In that case the source reset to HEAD immediately creates a content difference, making compression harmless.

### `zodbsync move` skips mixed-layer descendants

Recursive `zodbsync move` skips descendants whose `obj.zodbsync_layer` already differs from the source layer. To move such descendants, name them explicitly as additional `zodbsync move` invocations. There is no `--force` flag.
