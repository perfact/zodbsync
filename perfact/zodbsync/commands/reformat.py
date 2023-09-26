#!/usr/bin/python3

import os

from ..subcommand import SubCommand
from ..helpers import StrRepr, literal_eval
from ..zodbsync import mod_format


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
        commits_raw = self.gitcmd_output(
            'log', '--format=%H', '--reverse',
            '{}..HEAD'.format(start)
        )
        commits = [c for c in commits_raw.strip().split('\n') if c]

        self.gitcmd_run('reset', '--hard', start)
        base = self.config['base_dir']
        paths = []
        for root, dirs, files in os.walk(os.path.join(base, '__root__')):
            if '__meta__' in files:
                paths.append(os.path.join(root, '__meta__'))
        if self.reformat(paths):
            self.gitcmd_run('commit', '-a', '-m', 'zodbsync reformat')
        for idx, commit in enumerate(commits):
            print("Processing commit {}/{}".format(idx+1, len(commits)))
            cur = self.head()
            paths = list({
                os.path.join(base, line)
                for line in self.gitcmd_output(
                    'diff', '--name-only', '--no-renames', commit + '~', commit
                ).strip().split('\n')
                if line
            })
            metas = {path for path in paths if path.endswith('/__meta__')}
            if self.reformat(metas, True):
                self.gitcmd_run('commit', '-a', '-m', 'reverse')
            self.gitcmd_run('checkout', '--no-overlay', commit, '--', *paths)
            self.gitcmd_try('commit', '--no-edit', '-c', commit)
            self.reformat(metas)
            # Squash commits together with original message
            self.gitcmd_run('reset', cur)
            while paths:
                self.gitcmd_run('add', *paths[:100])
                del paths[:100]
            self.gitcmd_try('commit', '--no-edit', '-c', commit)

    def reformat(self, paths, legacy=False):
        changed = False
        for path in paths:
            if not os.path.exists(path):
                continue
            for _ in range(1000):  # Just in case we have no stdin
                with open(path) as f:
                    orig = f.read()
                try:
                    data = literal_eval(orig)
                except Exception:
                    # This allows the user to open the file manually, make it
                    # valid to be parsed by literal_eval and continue the
                    # process
                    print("Unable to parse path", path)
                    input("Retrying. Ctrl+C to cancel.")
                    continue
                break
            else:
                raise ValueError("Meta file could not be parsed")
            if legacy:
                fmt = StrRepr()(data, legacy=True)
            else:
                fmt = mod_format(data)
            if orig == fmt:
                continue
            with open(path, 'w') as f:
                f.write(fmt)
            changed = True

        return changed
