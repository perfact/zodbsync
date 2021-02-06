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
            '--dry-run', action='store_true', default=False,
            help='Only check for conflicts and roll back at the end.',
        )
        parser.add_argument(
            '--grep', type=str, help="""Find commits starting from the given
            ones, limiting to those with commit messages matching the
            pattern - like "git log --grep".""",
        )
        parser.add_argument(
            'commit', type=str, nargs='*',
            help='''Commits that are checked for compatibility and applied,
            playing back all affected paths at the end.'''
        )

    @SubCommand.with_lock
    def run(self):
        # Check for unstaged changes
        self.check_repo()

        changed_files = set()

        commits = []
        if self.args.grep:
            commits = self.gitcmd_output(
                'log', '--grep', self.args.grep,
                '--format=%H', '--reverse', *self.args.commit
            ).split('\n')
        else:
            for commit in self.args.commit:
                if '..' in commit:
                    # commit range
                    commits.extend(self.gitcmd_output(
                        'log', '--format=%H', '--reverse', commit
                    ).split('\n'))
                else:
                    commits.append(commit)

        for commit in commits:
            if not commit:
                continue
            self.logger.info('Checking and applying %s.' % commit)
            # obtain files affected by the commit
            files = [
                line for line in self.gitcmd_output(
                    'diff-tree', '--no-commit-id', '--name-only', '-r', commit
                ).strip().split('\n')
                if line
            ]

            # Check that none of the files is present in the stashed away
            # unstaged changes
            if len([f for f in files if f in self.unstaged_changes]):
                self.abort()
                raise Exception(
                    'Unable to apply %s, it touches unstaged files.'
                    % commit
                )

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
                self.abort()
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
                dryrun=self.args.dry_run,
            )
            if self.args.dry_run:
                self.abort()
                return

            if self.unstaged_changes:
                self.gitcmd_run('stash', 'pop')

        except Exception:
            self.logger.exception('Error playing back objects. Resetting.')
            self.abort()
            raise
