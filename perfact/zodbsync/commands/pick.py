#!/usr/bin/env python

import subprocess

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
            'commit', type=str, nargs='*',
            help='''Commits that are checked for compatibility and applied,
            playing back all affected paths at the end.'''
        )

    def gitcmd(self, *args):
        return ['git', '-C', self.sync.base_dir] + list(args)

    def gitcmd_run(self, *args):
        '''Wrapper to run a git command.'''
        subprocess.check_call(self.gitcmd(*args))

    def gitcmd_output(self, *args):
        '''Wrapper to run a git command and return the output.'''
        return subprocess.check_output(
            self.gitcmd(*args), universal_newlines=True
        )

    def run(self):
        self.sync.acquire_lock()
        # Check that there are no unstaged changes
        assert len(self.gitcmd_output('status', '--porcelain')) == 0, (
            "You have unstaged changes. Please commit or stash them."
        )

        orig_commit = self.gitcmd_output(
            'show-ref', '--head', '--hash', 'HEAD',
        ).strip()

        changed_files = set()

        commits = []
        for commit in self.args.commit:
            if '..' in commit:
                # commit range
                commits.extend([
                    c for c in self.gitcmd_output(
                        'log', '--format=%H', '--reverse', commit
                    ).split('\n')
                    if c
                ])
            else:
                commits.append(commit)

        for commit in commits:
            self.logger.info('Checking and applying %s.' % commit)
            # obtain files affected by the commit
            files = [
                line for line in self.gitcmd_output(
                    'diff-tree', '--no-commit-id', '--name-only', '-r', commit
                ).strip().split('\n')
                if line
            ]

            # check if these files currently differ from their state *before*
            # the given commit
            try:
                self.gitcmd_run(
                    'diff', '--exit-code', commit+'~', 'HEAD', '--', *files
                )
            except subprocess.CalledProcessError:
                self.logger.error(
                    'Unable to apply %s due to the above differences.'
                    % commit
                )
                self.gitcmd_run('reset', '--hard', orig_commit)
                raise

            changed_files.update(files)
            # capture output and discard so we don't clutter stdout
            # Python 2 has no subprocess.DEVNULL.
            self.gitcmd_output('cherry-pick', '-Xno-renames', commit)

        paths = sorted({
            filename[len(self.sync.site):].rsplit('/', 1)[0]
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
            self.gitcmd_run('reset', '--hard', orig_commit)
            raise
