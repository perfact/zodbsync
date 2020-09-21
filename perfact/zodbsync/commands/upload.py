#!/usr/bin/env python

from ..subcommand import SubCommand


class Upload(SubCommand):
    '''Upload a folder structure, e.g. a JS library, to zope Data.fs
    XXX: Start with a cheap copy of playback command
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
            '--dry-run', action='store_true', default=False,
            help='Roll back at the end.',
        )
        parser.add_argument(
            'target', type=str,
            help='Path of target library folder',
        )
        parser.add_argument(
            'path', type=str,
            help='Sub-Path in Data.fs to put target folder',
        )

    def run(self):
        print("yay my first own command!")
        '''
        XXX: we will need pretty much the same but convert target folder
        into __meta__ and __source__ files before and move to repo
        self.sync.acquire_lock()
        self.sync.playback_paths(
            paths=self.args.path,
            recurse=not self.args.no_recurse,
            override=self.args.override,
            skip_errors=self.args.skip_errors,
            dryrun=self.args.dry_run,
        )
        self.sync.release_lock()
        '''
