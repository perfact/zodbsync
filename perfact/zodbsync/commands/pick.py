#!/usr/bin/env python

from ..subcommand import SubCommand


class Pick(SubCommand):
    '''Cherry-pick commits, apply them and play back affected objects'''
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

    @SubCommand.gitexec
    def run(self):
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
            # capture output and discard so we don't clutter stdout
            # Python 2 has no subprocess.DEVNULL.
            self.gitcmd_output('cherry-pick', '--strategy', 'resolve', commit)
