#!/usr/bin/env python

import subprocess

from ..subcommand import SubCommand

class WithLock(SubCommand):
    """
    SubCommand to execute a shell command by first grabbing the lock.
    """
    subcommand = 'with-lock'

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'command', type=str, help="Shell-command to be executed",
        )

    def run(self):
        self.sync.acquire_lock()
        subprocess.run(self.args.command, shell=True)
