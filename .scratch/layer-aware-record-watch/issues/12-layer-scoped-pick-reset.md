Status: done

# Layer-scoped `pick` and multi-layer `reset`

## Parent

`.scratch/layer-aware-record-watch/PRD.md`

## What to build

Extend `zodbsync pick` with a `--layer <ident>` flag and extend `zodbsync reset` with a
`<ident>:<targetref>` positional syntax, so both commands can operate on named layer repos
instead of always targeting `base_dir`.

### Shared infrastructure: layer-aware `gitcmd`

`SubCommand` gains a `_git_workdir` attribute (default: `self.config["base_dir"]`). All
`gitcmd`/`gitcmd_run`/`gitcmd_try`/`gitcmd_output` calls use `-C self._git_workdir` instead
of `-C self.config["base_dir"]`. `check_repo` and `abort` also operate on `self._git_workdir`.

Layer-scoped commands set `self._git_workdir` to the target layer's `workdir` before the
`gitexec`-decorated method runs.

### `pick` changes

```
zodbsync pick [--layer <ident>] [existing flags] [commits...]
```

- `--layer <ident>` resolves the ident to a layer config entry. Empty string or absent →
  custom layer (`base_dir`). Behavior in that case is identical to today.
- When a named layer is given, set `self._git_workdir = layer["workdir"]` before calling
  `run()`. All git operations (cherry-pick, diff, stash check, abort) run in that repo.
- Before starting, check the named layer's workdir for unstaged changes via
  `gitcmd_output("status", "--untracked-files", "-z")`. If any exist, exit with an error —
  no stash/restore for named-layer pick.
- Changed paths are collected by diffing `orig_commit..HEAD` in the named layer's workdir.
  Paths are fed to `_playback_paths` exactly as today.

### `reset` changes

```
zodbsync reset <ident>:<targetref> [<ident>:<targetref> ...]   # new
zodbsync reset <commit>                                         # existing — custom layer
```

Parsing: split each positional on the first `:`. If `:` present → `(ident, targetref)`.
If absent → `("", argument)` (custom layer, backward-compatible). Empty ident resolves to
custom layer.

Execution (all-or-nothing):

1. For each target, resolve ident → layer, record `orig_commit` (current HEAD), stash
   unstaged changes if any (`git stash push --include-untracked` in that layer's workdir).
2. For each target in order, `git reset --hard <targetref>` in that layer's workdir.
3. For each target, diff `orig_commit..HEAD` and accumulate the union of changed paths
   across all layers.
4. Call `_playback_paths` once on the union.
5. On any failure in steps 2–4: for every layer already reset, `git reset --hard
   <orig_commit>`; pop all stashes. Re-raise.
6. On success: pop stashes for layers that had unstaged changes.

Conflict detection (unstaged files ∩ changed files) is performed per layer with that
layer's own unstaged file set. If any conflict is found, abort before touching any layer.

## Acceptance criteria

- [x] `pick --layer <ident>` cherry-picks in the named layer's workdir; custom layer workdir unchanged.
- [x] `pick` with no `--layer` is identical to current behavior.
- [x] `pick --layer <ident>` with dirty named-layer workdir fails before cherry-pick.
- [x] `reset <ident>:<commit>` resets named layer's repo; plays back changed objects; custom layer unchanged.
- [x] `reset <ident1>:<commit1> <ident2>:<commit2>` resets both repos; union of changed paths played back in one pass.
- [x] `reset <commit>` (bare) is identical to current behavior.
- [x] Multi-layer `reset` rolls back all layers if any step fails.
- [x] Multi-layer `reset` stashes and restores unstaged changes in each target layer.
- [x] Test: `pick --layer` in named layer — FS state and played-back objects correct; custom layer untouched.
- [x] Test: `pick --layer` dirty workdir — clean failure, no cherry-pick attempted.
- [x] Test: `reset <ident>:<commit>` single named layer.
- [x] Test: `reset <ident1>:<commit1> <ident2>:<commit2>` — atomic playback.
- [x] Test: `reset <commit>` bare — unchanged behavior.
- [x] Test: multi-layer reset mid-failure — all layers restored to orig commits.

## Blocked by

None. Independent of the record/watch/move work. Can be implemented in parallel.
