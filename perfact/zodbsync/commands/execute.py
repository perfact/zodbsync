#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand


class Exec(SubCommand):
    """Execute a command and play back any paths changed between old and new
    HEAD"""

    def __init__(self, **kw):
        super().__init__(**kw)
        layer_ident = getattr(self.args, "layer", None)
        if layer_ident is not None:
            layer = next(
                (la for la in self.sync.layers if la["ident"] == layer_ident),
                None,
            )
            if layer is None:
                raise SystemExit(f"Unknown layer ident: {layer_ident!r}")
            self._git_workdir = layer["workdir"]

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

    def _git_run(self, workdir, *args):
        subprocess.check_call(["git", "--no-pager", "-C", workdir] + list(args))

    def _git_output(self, workdir, *args):
        return subprocess.check_output(
            ["git", "--no-pager", "-C", workdir] + list(args), universal_newlines=True
        )

    def _get_head(self, workdir):
        return self._git_output(workdir, "rev-parse", "HEAD").strip()

    def _get_unstaged(self, workdir):
        raw = self._git_output(workdir, "status", "--untracked-files", "-z")
        return [line[3:] for line in raw.split("\0") if line]

    def _get_changed(self, workdir, orig):
        output = self._git_output(workdir, "diff", orig, "--name-only", "--no-renames")
        return {line for line in output.strip().split("\n") if line}

    def run(self):
        if self.args.nocd and self.args.layer is None:
            self._run_all_layers()
        else:
            self._run_single_layer()

    @SubCommand.gitexec
    def _run_single_layer(self):
        cwd = (
            None
            if self.args.nocd
            else getattr(self, "_git_workdir", self.sync.base_dir)
        )
        subprocess.check_call(self.args.cmd, cwd=cwd, shell=True)

    @SubCommand.with_lock
    def _run_all_layers(self):
        targets = [
            {
                "workdir": la["workdir"],
                "orig_commit": self._get_head(la["workdir"]),
                "unstaged": self._get_unstaged(la["workdir"]),
            }
            for la in self.sync.layers
        ]

        stashed = []
        for t in targets:
            if t["unstaged"]:
                self.logger.warning("Unstaged changes in %s, stashing.", t["workdir"])
                self._git_run(t["workdir"], "stash", "push", "--include-untracked")
                stashed.append(t)

        paths = []
        try:
            subprocess.check_call(self.args.cmd, cwd=None, shell=True)

            all_files = set()
            for t in targets:
                all_files |= self._get_changed(t["workdir"], t["orig_commit"])
            paths = sorted(f for f in all_files if f.startswith(self.sync.site))

            if self.args.dry_run:
                for t in targets:
                    self._git_run(t["workdir"], "reset", "--hard", t["orig_commit"])
                for t in stashed:
                    self._git_run(t["workdir"], "stash", "pop")
                return

            self._playback_paths(paths)

        except Exception:
            self.logger.error("Error during operation. Resetting.")
            for t in targets:
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

        for t in stashed:
            self._git_run(t["workdir"], "stash", "pop")
