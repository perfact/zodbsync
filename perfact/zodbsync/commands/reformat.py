#!/usr/bin/python3

import os

from ..subcommand import SubCommand
from ..helpers import StrRepr, literal_eval


class Reformat(SubCommand):
    """
    Rewrite commits from given commit to HEAD to post-4.3.2 formatting
    """
    # Note that in contrast to most subcommands, this should probably be
    # executed on a local repository not directly in sync with a ZODB.  No
    # rollback in case of error is implemented yet!

    connect = False

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'commit', type=str,
            help="Starting point before first commit to rewrite"
        )

    def head(self):
        return self.gitcmd_output('rev-parse', 'HEAD').strip()

    @SubCommand.with_lock
    def run(self):
        start = self.args.commit
        commits = self.gitcmd_output(
            'log', '--format=%H', '--reverse',
            '{}..HEAD'.format(start)
        ).strip().split('\n')

        self.gitcmd_run('reset', '--hard', start)
        paths = []
        for root, dirs, files in os.walk(self.config['base_dir']):
            if '__meta__' in files:
                paths.append(os.path.join(root, '__meta__'))
        if self.reformat(paths):
            self.gitcmd_run('commit', '-a', '-m', 'zodbsync reformat')
        for commit in commits:
            cur = self.head()
            paths = list({
                line for line in self.gitcmd_output(
                    'diff', '--name-only', '--no-renames', commit + '~', commit
                ).strip().split('\n')
                if line
            })
            metas = {path for path in paths if path.endswith('/__meta__')}
            if self.reformat(metas, True):
                self.gitcmd_run('commit', '-a', '-m', 'reverse')
            self.gitcmd_run('cherry-pick', '-Xno-renames', commit)
            self.reformat(metas)
            # Squash commits together with original message
            self.gitcmd_run('reset', cur)
            while paths:
                self.gitcmd_run('add', *paths[:100])
                del paths[:100]
            self.gitcmd_run('commit', '--no-edit', '-c', commit)

    def reformat(self, paths, legacy=False):
        changed = False
        for path in paths:
            if not os.path.exists(path):
                continue
            with open(path) as f:
                orig = f.read()
            data = literal_eval(orig)
            rules = {
                'perms': [4],
                'props': [5],
                'local_roles': [4],
            }
            if legacy:
                fmt = StrRepr()(data, legacy=True)
            else:
                fmt = StrRepr()(data, rules)
            if orig == fmt:
                continue
            with open(path, 'w') as f:
                f.write(fmt)
            changed = True

        return changed
