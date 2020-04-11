#!/usr/bin/env python

from ..subcommand import SubCommand

class Apply(SubCommand):
    '''Sub-command to apply patches and play back changed files.'''

    def add_args(self, parser):
        parser.add_argument(
            'patchfile', type=str, nargs='+',
            help='''Patch files which are applied to the repository (using git
            am). If successful, the changed objects are played back. Else, the
            am session is automatically rolled back.''',
        )

