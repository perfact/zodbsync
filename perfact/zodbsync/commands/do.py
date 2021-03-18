#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand


class Do(SubCommand):
    '''Execute a command and play back any paths changed between commits'''
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
            'command', type=str, help='''command to be executed'''
        )

    @SubCommand.with_lock
    @SubCommand.gitop
    def run(self):
        subprocess.check_output(self.args.command, shell=True)
