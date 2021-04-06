#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand


class WithLock(SubCommand):
    """Execute a shell command by first grabbing the lock"""
    subcommand = 'with-lock'
    connect = False

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'cmd', type=str, help="Shell-command to be executed",
        )

    @SubCommand.with_lock
    def run(self):
        subprocess.check_call(self.args.cmd, shell=True)
