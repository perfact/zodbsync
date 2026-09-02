#!/usr/bin/env python

import subprocess

from ..helpers import git_changed, git_head, git_run, git_unstaged
from ..subcommand import SubCommand


class Exec(SubCommand):
    """Execute a command and play back any paths changed between old and new
    HEAD"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._set_layer()

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            "--layer",
            type=str,
            default=None,
            help=(
                "Named layer ident to run command in (empty string for fallback layer)"
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
        targets = [
            {
                "workdir": la["workdir"],
                "orig_commit": git_head(la["workdir"]),
                "unstaged": git_unstaged(la["workdir"]),
            }
            for la in self.sync.layers
        ]

        stashed = []
        for t in targets:
            if t["unstaged"]:
                self.logger.warning("Unstaged changes in %s, stashing.", t["workdir"])
                git_run(t["workdir"], "stash", "push", "--include-untracked")
                stashed.append(t)

        paths = []
        try:
            subprocess.check_call(self.args.cmd, cwd=None, shell=True)

            all_files = set()
            for t in targets:
                all_files |= git_changed(t["workdir"], t["orig_commit"])
            paths = sorted(f for f in all_files if f.startswith(self.sync.site))

            if self.args.dry_run:
                for t in targets:
                    git_run(t["workdir"], "reset", "--hard", t["orig_commit"])
                for t in stashed:
                    git_run(t["workdir"], "stash", "pop")
                return

            self._playback_paths(paths)

        except Exception:
            self.logger.error("Error during operation. Resetting.")
            for t in targets:
                try:
                    git_run(t["workdir"], "reset", "--hard", t["orig_commit"])
                except Exception:
                    self.logger.exception("Failed to rollback %s", t["workdir"])
            for t in stashed:
                try:
                    git_run(t["workdir"], "stash", "pop")
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

        for t in stashed:
            git_run(t["workdir"], "stash", "pop")
