#!/usr/bin/env python

from ..subcommand import SubCommand


class Reset(SubCommand):
    '''Reset to some other commit and play back any changed paths'''
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
            'commit', type=str,
            help='''Target commit'''
        )

    @SubCommand.gitexec
    def run(self):
        target = self.args.commit
        self.logger.info('Checking and resetting to %s.' % target)
        self.gitcmd_run('reset', '--hard', target)
