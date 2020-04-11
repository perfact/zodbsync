#!/usr/bin/env python

from ..subcommand import SubCommand


class Playback(SubCommand):
    ''' Sub-command to play back objects from the file system to the Data.fs.
    '''
    def add_args(self, parser):
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

    def run(self, args, sync):
        paths = args.path
        if not paths:
            return
        recurse = not args.no_recurse
        if recurse:
            sync.remove_redundant_paths(paths)

        sync.acquire_lock()
        note = 'perfact-zopeplayback'
        if len(paths) == 1:
            note += ': ' + paths[0]
        txn_mgr = sync.start_transaction(note=note)

        try:
            for path in paths:
                sync.playback(
                    path=path,
                    override=args.override,
                    recurse=recurse,
                    skip_errors=args.skip_errors,
                )
        except Exception:
            print('Error with path ' + path)
            txn_mgr.abort()
            raise
        finally:
            txn_mgr.commit()
        sync.release_lock()
