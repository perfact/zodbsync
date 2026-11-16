#!/usr/bin/env python

import subprocess

from ..helpers import (
    git_abort,
    git_branch_info,
    git_changed,
    git_head,
    git_run,
    git_state_clean,
    git_unstaged,
)
from ..subcommand import SubCommand


class Exec(SubCommand):
    """Execute a command and play back any paths changed between old and new
    HEAD"""

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            "--layer",
            type=str,
            default=None,
            help=(
                "Named layer ident to run command in (empty string for fallback"
                " layer). If omitted, the named layer whose workdir the cwd is"
                " in is used, if any."
            ),
        )
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
            "--nocd",
            action="store_true",
            default=False,
            help="Do not cd to git repo for command",
        )
        parser.add_argument("cmd", type=str, help="""command to be executed""")

    def run(self):
        if self.args.nocd and self.args.layer is None:
            self._run_all_layers()
        else:
            self._run_single_layer()

    @SubCommand.gitexec
    def _run_single_layer(self):
        cwd = None if self.args.nocd else self._git_workdir
        subprocess.check_call(self.args.cmd, cwd=cwd, shell=True)

    @SubCommand.with_lock
    def _run_all_layers(self):
        targets = []
        for la in self.sync.layers:
            wd = la["workdir"]
            orig_branch, branches = git_branch_info(wd)
            targets.append(
                {
                    "workdir": wd,
                    "orig_commit": git_head(wd),
                    "orig_branch": orig_branch,
                    "branches": branches,
                    "unstaged": git_unstaged(wd),
                }
            )

        for t in targets:
            if t["unstaged"]:
                self.logger.warning("Unstaged changes in %s, stashing.", t["workdir"])
                git_run(t["workdir"], "stash", "push", "--include-untracked")
            t["stashed"] = bool(t["unstaged"])

        paths = []
        try:
            subprocess.check_call(self.args.cmd, cwd=None, shell=True)

            all_files = set()
            for t in targets:
                assert git_state_clean(t["workdir"]), "Git state not clean"
                files = git_changed(t["workdir"], t["orig_commit"])
                conflicts = files & set(t["unstaged"])
                assert not conflicts, "Change in unstaged files, aborting"
                all_files |= files
            paths = sorted(f for f in all_files if f.startswith(self.sync.site))

            self._playback_paths(paths)

            if self.args.dry_run:
                self._abort_layers(targets)
                return

        except Exception:
            self.logger.error("Error during operation. Resetting.")
            self._abort_layers(targets)
            # We can't be sure that nothing was committed to the Data.FS
            # before the error, so play back all paths again
            if not self.args.dry_run and paths:
                self.sync.playback_paths(
                    paths=self.sync.prepare_paths(paths),
                    recurse=False,
                    override=True,
                    skip_errors=True,
                    dryrun=False,
                )
            raise

        for t in targets:
            if t["stashed"]:
                git_run(t["workdir"], "stash", "pop")

    def _abort_layers(self, targets):
        """Restore branches and stashes in all layers, ignoring failures of
        single layers so the others are still restored."""
        for t in targets:
            try:
                git_abort(t["workdir"], t["orig_branch"], t["branches"], t["stashed"])
            except Exception:
                self.logger.exception("Failed to roll back %s", t["workdir"])
