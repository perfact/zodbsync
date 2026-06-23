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

    @SubCommand.gitexec
    def run(self):
        if self.args.nocd:
            cwd = None
        else:
            cwd = getattr(self, "_git_workdir", self.sync.base_dir)
        subprocess.check_call(self.args.cmd, cwd=cwd, shell=True)
