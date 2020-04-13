#!/usr/bin/env python

import subprocess
import sys
import os

from ..subcommand import SubCommand


class Pick(SubCommand):
    ''' Sub-command to cherry-pick commits, apply them and play back affected
    objects.
    '''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--skip-errors', action='store_true', default=False,
            help='Skip failed objects and continue',
        )
        parser.add_argument(
            'commit', type=str, nargs='+',
            help='''Commits that are checked for compatibility and applied,
            playing back all affected paths at the end.'''
        )

    def gitcmd(self, *args):
        return ['git', '-C', self.sync.base_dir] + list(args)

    def run(self):
        self.sync.acquire_lock()
        # Check that there are no unstaged changes
        dirty_workdir = len(subprocess.check_output(
            self.gitcmd('status', '--porcelain'),
        )) > 0
        if dirty_workdir:
            self.logger.error("You have unstaged changes. Please commit or"
                              " stash them")
            sys.exit(1)

        orig_commit = subprocess.check_output(
            self.gitcmd('show-ref', '--head', 'HEAD'),
            universal_newlines=True
        ).split()[0]

        changed_files = set()

        # compatibility since python2 has no subprocess.DEVNULL
        fnull = open(os.devnull, 'w')

        commits = self.args.commit
        for commit in commits:
            self.logger.info('Checking and applying %s.' % commit)
            # obtain files affected by the commit
            files = [
                line for line in subprocess.check_output(
                    self.gitcmd('diff-tree', '--no-commit-id', '--name-only',
                                '-r', commit),
                    universal_newlines=True
                ).strip().split('\n')
                if line
            ]

            # check if these files currently differ from their state *before*
            # the given commit
            try:
                subprocess.check_call(
                    self.gitcmd('diff', '--exit-code', commit+'~', 'HEAD',
                                '--', *files)
                )
            except subprocess.CalledProcessError:
                self.logger.error(
                    'Unable to apply %s due to the above differences.'
                    % commit
                )
                subprocess.check_call(
                    self.gitcmd('reset', '--hard', orig_commit)
                )
                raise

            changed_files.update(files)
            subprocess.check_call(
                self.gitcmd('cherry-pick', commit),
                stdout=fnull
            )

        paths = sorted({
            filename[len('__root__'):].rsplit('/', 1)[0]
            for filename in changed_files
        })

        try:
            self.sync.playback_paths(
                paths=paths,
                recurse=False,
                override=True,
                skip_errors=self.args.skip_errors,
            )
        except Exception:
            self.logger.exception('Error when playing back objects.'
                                  ' Resetting.')
            subprocess.check_call(self.gitcmd('reset', '--hard', orig_commit))
            raise
