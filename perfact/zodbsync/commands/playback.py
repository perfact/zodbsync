#!/usr/bin/env python

from ..subcommand import SubCommand


class Playback(SubCommand):
    '''Play back objects from the file system to the Data.FS'''
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
            '--dry-run', action='store_true', default=False,
            help='Roll back at the end.',
        )
        parser.add_argument(
            'path', type=str, nargs='*',
            help='Sub-Path in Data.fs to be played back',
        )

    @SubCommand.with_lock
    def run(self):
        self.sync.playback_paths(
            paths=self.args.path,
            recurse=not self.args.no_recurse,
            override=self.args.override,
            skip_errors=self.args.skip_errors,
            dryrun=self.args.dry_run,
        )
