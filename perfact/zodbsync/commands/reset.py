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

    @SubCommand.with_lock
    def run(self):
        # Check for unstaged changes
        self.check_repo()
        target = self.args.commit
        self.logger.info('Checking and resetting to %s.' % target)

        try:
            changed_files = self.gitcmd_output(
                'diff', '--name-only', target, 'HEAD'
            ).strip().split('\n')

            conflicts = [
                f for f in changed_files
                if f in self.unstaged_changes
            ]
            assert len(conflicts) == 0, (
                "Unable to reset, unstaged files would be changed."
            )
            self.gitcmd_run('reset', '--hard', target)

            paths = sorted({
                filename[len(self.sync.site):].rsplit('/', 1)[0]
                for filename in changed_files
            })

            self.sync.playback_paths(
                paths=paths,
                recurse=False,
                override=True,
                skip_errors=self.args.skip_errors,
                dryrun=self.args.dry_run,
            )

        except Exception:
            self.logger.exception('Error resetting to commit. Aborting.')
            self.abort()
            raise

        if self.args.dry_run:
            self.abort()
        elif self.unstaged_changes:
            self.gitcmd_run('stash', 'pop')
