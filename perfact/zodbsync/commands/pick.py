#!/usr/bin/env python

from ..subcommand import SubCommand


class Pick(SubCommand):
    ''' Sub-command to cherry-pick commits, apply them and play back affected
    objects.
    '''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'commit', type=str, nargs='+',
            help='''Commits that are checked for compatibility and applied,
            playing back all affected paths at the end.'''
        )
