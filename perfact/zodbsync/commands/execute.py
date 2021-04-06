#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand


class Exec(SubCommand):
    '''Execute a command and play back any paths changed between old and new
    HEAD'''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--skip-errors', action='store_true', default=False,
            help='Skip failed objects and continue',
        )
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Only check for conflicts and roll back at the end.',
        )
        parser.add_argument(
            '--nocd', action='store_true', default=False,
            help='Do not cd to git repo for command',
        )
        parser.add_argument(
            'cmd', type=str, help='''command to be executed'''
        )

    @SubCommand.gitexec
    def run(self):
        subprocess.check_call(
            self.args.cmd,
            cwd=None if self.args.nocd else self.sync.base_dir,
            shell=True,
        )
