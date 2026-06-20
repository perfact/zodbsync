#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand


class Reset(SubCommand):
    """Reset to some other commit and play back any changed paths"""

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            "--skip-errors",
            action="store_true",
            default=False,
            help="Skip failed objects and continue",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Only check for conflicts and roll back at the end.",
        )
        parser.add_argument(
            "commit",
            type=str,
            nargs="+",
            help="""Target commit(s). Bare <commit> resets the fallback layer.
            <ident>:<commit> resets the named layer with that ident.""",
        )

    def _find_layer(self, ident):
        for layer in self.sync.layers:
            if layer["ident"] == ident:
                return layer
        return None

    def _git(self, workdir, *args):
        return ["git", "--no-pager", "-C", workdir] + list(args)

    def _git_run(self, workdir, *args):
        subprocess.check_call(self._git(workdir, *args))

    def _git_try(self, workdir, *args):
        return subprocess.call(self._git(workdir, *args))

    def _git_output(self, workdir, *args):
        return subprocess.check_output(
            self._git(workdir, *args), universal_newlines=True
        )

    def _get_head(self, workdir):
        return self._git_output(workdir, "rev-parse", "HEAD").strip()

    def _get_unstaged(self, workdir):
        raw = self._git_output(workdir, "status", "--untracked-files", "-z")
        return [line[3:] for line in raw.split("\0") if line]

    def _get_changed(self, workdir, orig):
        output = self._git_output(workdir, "diff", orig, "--name-only", "--no-renames")
        return {line for line in output.strip().split("\n") if line}

    @SubCommand.with_lock
    def run(self):
        # Detect bare single-target (no colon) for backward-compat rollback hint
        is_bare = len(self.args.commit) == 1 and ":" not in self.args.commit[0]

        # Parse targets
        targets = []
        for arg in self.args.commit:
            if ":" in arg:
                ident, ref = arg.split(":", 1)
            else:
                ident, ref = "", arg
            layer = self._find_layer(ident)
            if layer is None:
                raise SystemExit(f"Unknown layer ident: {ident!r}")
            wd = layer["workdir"]
            targets.append(
                {
                    "layer": layer,
                    "ref": ref,
                    "workdir": wd,
                    "orig_commit": self._get_head(wd),
                    "unstaged": self._get_unstaged(wd),
                }
            )

        # Conflict detection: predict which files will change and check against
        # unstaged files — abort before touching any layer.
        for t in targets:
            ref_range = t["orig_commit"] + ".." + t["ref"]
            predicted = self._get_changed(t["workdir"], ref_range)
            conflicts = predicted & set(t["unstaged"])
            if conflicts:
                raise SystemExit(
                    "Change in unstaged files, aborting: {}".format(conflicts)
                )

        # Stash unstaged changes in each target layer
        stashed = []
        for t in targets:
            if t["unstaged"]:
                self.logger.warning("Unstaged changes in %s, stashing.", t["workdir"])
                self._git_run(t["workdir"], "stash", "push", "--include-untracked")
                stashed.append(t)

        reset_done = []
        paths = []
        try:
            # Reset each target layer
            for t in targets:
                self.logger.info(
                    "Checking and resetting to %s in %s.", t["ref"], t["workdir"]
                )
                self._git_run(t["workdir"], "reset", "--hard", t["ref"])
                reset_done.append(t)

            # Accumulate union of changed paths across all layers
            all_files = set()
            for t in targets:
                all_files |= self._get_changed(t["workdir"], t["orig_commit"])

            paths = sorted(f for f in all_files if f.startswith(self.sync.site))

            if self.args.dry_run:
                for t in reset_done:
                    self._git_run(t["workdir"], "reset", "--hard", t["orig_commit"])
                for t in stashed:
                    self._git_run(t["workdir"], "stash", "pop")
                return

            self._playback_paths(paths)

        except Exception:
            self.logger.error("Error during operation. Resetting.")
            for t in reset_done:
                try:
                    self._git_run(t["workdir"], "reset", "--hard", t["orig_commit"])
                except Exception:
                    self.logger.exception("Failed to rollback %s", t["workdir"])
            for t in stashed:
                try:
                    self._git_run(t["workdir"], "stash", "pop")
                except Exception:
                    self.logger.exception("Failed to pop stash in %s", t["workdir"])
            if not self.args.dry_run and paths:
                self.sync.playback_paths(
                    paths=self.sync.prepare_paths(paths),
                    recurse=False,
                    override=True,
                    skip_errors=True,
                    dryrun=False,
                )
            raise

        # Pop stashes on success
        for t in stashed:
            self._git_run(t["workdir"], "stash", "pop")

        # Emit rollback command hint for single bare-target (backward compat)
        if is_bare:
            wd = targets[0]["workdir"]
            orig = targets[0]["orig_commit"]
            is_ancestor = (
                self._git_try(wd, "merge-base", "--is-ancestor", orig, "HEAD") == 0
            )
            if is_ancestor:
                merge_commits = self._git_output(
                    wd,
                    "log",
                    "--oneline",
                    "--min-parents=2",
                    f"{orig}..HEAD",
                ).strip()
                if not merge_commits:
                    head_commit = self._git_output(wd, "rev-parse", "HEAD").strip()
                    cmd = f'zodbsync exec "git revert {orig}..{head_commit}"'
                    self.logger.info("Prepared Command for Rollback:\n%s", cmd)
