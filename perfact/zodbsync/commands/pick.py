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

    def check_repo(self):
        '''Check for unstaged changes and memorize current commit after
        acquiring lock. Move unstaged changes away via git stash'''
        self.unstaged_changes = [
            line[3:]
            for line in self.gitcmd_output(
                'status', '--untracked-files', '-z'
            ).split('\0')
            if line
        ]

        if self.unstaged_changes:
            self.logger.warning(
                "Unstaged changes found. Moving them out of the way."
            )
            self.gitcmd_run('stash', 'push', '--include-untracked')

        # The commit we reset to if something doesn't work out
        self.orig_commit = [
            line for line in self.gitcmd_output(
                'show-ref', '--head', 'HEAD',
            ).split('\n')
            if line.endswith(' HEAD')
        ][0].split()[0]

    def abort(self):
        '''Abort actions on repo and revert stash. check_repo must be
        called before this can be used'''
        self.gitcmd_run('reset', '--hard', self.orig_commit)
        if self.unstaged_changes:
            self.gitcmd_run('stash', 'pop')

    def run(self):
        self.sync.acquire_lock()
        # Check for unstaged changes
        self.check_repo()

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
