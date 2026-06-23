# exec: add --layer flag

Status: ready-for-agent

## What to build

Add `--layer <ident>` to `zodbsync exec` so it can run a command in a named layer's git workdir and play back only that layer's changes. Omitting `--layer` keeps current behavior (fallback layer). Follow the same pattern as `zodbsync pick`: resolve the layer in `__init__`, set `self._git_workdir`, let `@gitexec` handle the rest.

## Acceptance criteria

- [ ] `zodbsync exec --layer "ident" "cmd"` runs `cmd` in the named layer's workdir and plays back changed objects
- [ ] Unknown `--layer` ident exits with a clear error message
- [ ] `zodbsync exec "cmd"` (no `--layer`) is unchanged
- [ ] `--nocd` combined with `--layer` runs the command without cd but still checks only the specified layer

## Blocked by

None — can start immediately.
