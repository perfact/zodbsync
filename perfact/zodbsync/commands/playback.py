#!/usr/bin/env python

from ..subcommand import SubCommand


class Playback(SubCommand):
    ''' Sub-command to play back objects from the file system to the Data.fs.
    '''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--override', '-o', action='store_true',
            help='Override object type changes when uploading',
            default=False
        )
        parser.add_argument(
            '--no-recurse', action='store_true',
            help='''Only upload metadata, do not remove elements or recurse.
            Note: If a path no longer present on the file system is given, it
            is still removed.''',
            default=False
        )
        parser.add_argument(
            '--skip-errors', action='store_true',
            help="Skip failed objects and continue",
            default=False
        )
        parser.add_argument(
            'path', type=str, nargs='*',
            help='Sub-Path in Data.fs to be played back',
        )

    def run(self):
        self.sync.acquire_lock()
        self.sync.playback_paths(
            paths=self.args.path,
            recurse=not self.args.no_recurse,
            override=self.args.override,
            skip_errors=self.args.skip_errors,
        )
        self.sync.release_lock()
