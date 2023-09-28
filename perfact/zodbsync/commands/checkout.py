#!/usr/bin/env python

from ..subcommand import SubCommand


class Checkout(SubCommand):
    '''Switch to another branch'''
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
            '-b', action='store_true', default=False,
            help='Create branch.',
        )
        parser.add_argument(
            '-t', '--track', type=str, help='Set up upstream configuration.'
        )
        parser.add_argument(
            '--reset', type=str,
            help='Reset branch onto given commit.',
        )
        parser.add_argument(
            '--rebase', type=str,
            help='Rebase branch onto given commit.',
        )
        parser.add_argument(
            'branch', type=str,
            help='''Branch name'''
        )

    @SubCommand.gitexec
    def run(self):
        self.logger.info('Checking out %s.' % self.args.branch)
        cmd = ['checkout']
        if self.args.b:
            cmd.append('-b')
        cmd.append(self.args.branch)
        if self.args.b and self.args.track:
            cmd.extend(['--track', self.args.track])
        self.gitcmd_run(*cmd)
        if self.args.reset:
            self.gitcmd_run('reset', '--hard', self.args.reset)
        if self.args.rebase:
            self.gitcmd_run('rebase', self.args.rebase)
