# checkout: add --layer flag

Status: done

## What to build

Add `--layer <ident>` to `zodbsync checkout` so it can target a named layer's git repo instead of always operating on the fallback layer. The existing `--reset`, `--rebase`, `-b`, and `--track` options should work unchanged against the targeted layer. Backward compatibility is preserved: omitting `--layer` keeps current behavior (fallback layer).

Follow the same pattern as `zodbsync pick`: resolve the layer in `__init__`, set `self._git_workdir` to `layer["workdir"]`, and let `@gitexec` handle stash/diff/playback. If the ident is unknown, raise `SystemExit` with a clear message.

## Acceptance criteria

- [ ] `zodbsync checkout --layer "ident" <branch>` checks out `<branch>` in the named layer's workdir and plays back changed objects
- [ ] `--reset`, `--rebase`, `-b`, `--track` all work correctly when `--layer` is set
- [ ] Unknown `--layer` ident exits with a clear error message
- [ ] `zodbsync checkout <branch>` (no `--layer`) is unchanged
- [ ] Unstaged changes in the named layer workdir are stashed before checkout and restored after (via `@gitexec`)

## Blocked by

None — can start immediately.

## Comments

Implemented in `perfact/zodbsync/commands/checkout.py`: added `__init__` (resolves layer by ident, sets `self._git_workdir`, raises `SystemExit` on unknown ident) and `--layer` arg in `add_args`. `run()` unchanged — `@gitexec` handles stash/diff/playback via `self._git_workdir`. Tests: `test_checkout_layer`, `test_checkout_layer_unknown_ident`.
