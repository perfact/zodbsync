# ADR 0004: Session-scoped test environment with dual-fixture injection

## Status

Proposed

## Context

The integration test suite requires a live ZEO server, a git repository, and a ZODBSync config to be bootstrapped before any test can run. Bootstrapping takes several seconds.

The original `test_sync.py` is a single class with ~2600 lines. The `environment` fixture is class-scoped, so bootstrapping happens once per class — effectively once per test run. Splitting the file into domain-specific modules (record, playback, pick, upload, watch, types, extedit, layer, config) would create one class per file, and a naively class-scoped fixture would bootstrap nine times.

A function-scoped `envreset` fixture already handles inter-test state cleanup: it aborts any open transaction, hard-resets the git repo to the initial commit, deletes all non-autotest branches, and replays the initial ZODB state via `playback --skip-errors /`. Config mutations are handled by `appendtoconf`, a context manager that restores the config file after its block exits.

## Decision

The `environment` fixture in `conftest.py` is **session-scoped** — ZEO, git repo, and config are created once per test run and shared across all test files.

A separate **class-scoped** `inject_env` autouse fixture pulls from the session environment and injects its components as class attributes (`request.cls.zeo`, `request.cls.repo`, etc.). This is a cheap attribute assignment, not a reconstruction.

Helper methods (`run`, `gitrun`, `gitoutput`, `newconn`, etc.) live in `TestBase` in `tests/base.py`. All test classes inherit from it, preserving the `self.*` calling convention throughout without rewriting call sites.

`envreset` stays function-scoped and autouse. If state from one domain's tests is found to leak into another domain's tests, `envreset` is extended to cover the gap — not the fixture scope narrowed.

## Alternatives considered

**Module-scoped environment** — one ZEO per file, nine bootstraps. Bounds the blast radius of any state leak to a single file, but negates most of the speed benefit and doesn't eliminate the problem.

**Class-scoped (keep current)** — same as module-scoped when there is one class per file. Discarded for the same reason.

**Pytest-idiomatic fixtures returning callables** — replaces `TestBase` helper methods with fixtures. More aligned with pytest conventions but requires rewriting 60+ test method call sites with no functional benefit.

## Consequences

- Test suite bootstraps once regardless of how many domain files exist.
- `envreset` is the single point of responsibility for inter-test isolation. If a new test introduces side effects that `envreset` doesn't clean, all subsequent tests in the session may be affected. The fix is always to extend `envreset`, not to shrink the scope.
- The dual-scope pattern (session creation + class injection) is non-obvious to readers unfamiliar with pytest fixture scoping. This ADR is the explanation.
- `DummyResponse` and other test-local mocks remain in the files that use them.
