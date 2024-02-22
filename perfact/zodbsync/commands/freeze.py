#!/usr/bin/env python
import os

from ..subcommand import SubCommand


class Freeze(SubCommand):
    '''Mark paths as frozen and record them'''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'path', type=str, nargs='*',
            help='Sub-Path in Data.fs to be frozen',
        )

    @SubCommand.with_lock
    def run(self):
        for path in self.args.path:
            fullpath = self.sync.fs_path(path)
            os.makedirs(fullpath, exist_ok=True)
            with open('{}/__frozen__'.format(fullpath), 'w'):
                pass
        self.sync.record(paths=self.args.path, recurse=True)
