#!/usr/bin/env python

import sys
import subprocess
import os

import filelock

from .helpers import Namespace


class SubCommand(Namespace):
    '''
    Base class for different sub-commands to be used by zodbsync.
    '''

    # The presence of one of these in the .git folder indicates that some
    # process was not finished correctly, which is used to trigger a rollback
    # in some operations. Are these all?
    git_state_indicators = ['rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD',
                            'MERGE_HEAD', 'REVERT_HEAD']

    @staticmethod
    def add_args(parser):
        ''' Overwrite to add arguments specific to sub-command. '''
        pass

    def acquire_lock(self, timeout=10):
        if self.args.no_lock:
            return
        try:
            self.lock.acquire(timeout=1)
        except filelock.Timeout:
            self.logger.debug("Acquiring exclusive lock...")
            try:
                self.lock.acquire(timeout=timeout-1)
            except filelock.Timeout:
                self.logger.error("Unable to acquire lock.")
                sys.exit(1)

    def release_lock(self):
        if not self.args.no_lock:
            self.lock.release()

    @staticmethod
    def with_lock(func):
        """
        Decorator for instance methods that are enveloped by a lock
        """
        def wrapper(self, *args, **kwargs):
            self.acquire_lock()
            try:
                result = func(self, *args, **kwargs)
            finally:
                self.release_lock()
            return result

        return wrapper

    def gitcmd(self, *args):
        return ['git', '-P', '-C', self.sync.base_dir] + list(args)

    def gitcmd_run(self, *args):
        '''Wrapper to run a git command.'''
        subprocess.check_call(self.gitcmd(*args))

    def gitcmd_output(self, *args):
        '''Wrapper to run a git command and return the output.'''
        return subprocess.check_output(
            self.gitcmd(*args), universal_newlines=True
        )

    def datafs_filesystem_path(self, path):
        '''Create absolute filesystem path from Data.fs path
        '''

        if path.startswith('./'):
            path = path[2:]

        if path.startswith('/'):
            path = path[1:]

        data_fs_path = path
        if path.startswith(self.sync.site):
            filesystem_path = os.path.join(
                self.config["base_dir"],
                path
            )
        else:
            filesystem_path = os.path.join(
                self.config["base_dir"],
                self.sync.site,
                path
            )

        return data_fs_path, filesystem_path

    def _branch_info(self):
        """Returns currently checked out branch as well as where each branch
        points."""
        current = self.gitcmd_output(
            'rev-parse', '--abbrev-ref', 'HEAD'
        ).strip()

        branches = {}  # branchname -> commitid
        output = self.gitcmd_output('show-ref', '--heads')
        for line in output.strip().split('\n'):
            commit, refname = line.split()
            refname = refname[len('refs/heads/'):]
            branches[refname] = commit

        return (current, branches)

    def check_repo(self):
        '''Check for unstaged changes and memorize current commit. Move
        unstaged changes away via git stash'''
        self.unstaged_changes = [
            line[3:]
            for line in self.gitcmd_output(
                'status', '--untracked-files', '-z'
            ).split('\0')
            if line
        ]

        self.orig_branch, self.branches = self._branch_info()

        if self.unstaged_changes:
            self.logger.warning(
                "Unstaged changes found. Moving them out of the way."
            )
            self.gitcmd_run('stash', 'push', '--include-untracked')

        # The commit to compare to with regards to changed files
        self.orig_commit = self.branches[self.orig_branch]

    @staticmethod
    def gitexec(func):
        """
        Decorator for wrapping an operation with playback:
        - Stash unstaged changes away
        - memorize the current commit
        - do something
        - check for conflicts
        - play back changed objects (diff between old and new HEAD)
        - unstash
        """
        @SubCommand.with_lock
        def wrapper(self, *args, **kwargs):
            # Check for unstaged changes
            self.check_repo()

            try:
                func(self, *args, **kwargs)

                # Fail and roll back for any of the markers of an interrupted
                # git process (merge/rebase/cherry-pick/etc.)
                for fname in self.git_state_indicators:
                    path = os.path.join(self.sync.base_dir, '.git', fname)
                    assert not os.path.exists(path), "Git state not clean"

                files = {
                    line for line in self.gitcmd_output(
                        'diff', self.orig_commit, '--name-only', '--no-renames'
                    ).strip().split('\n')
                    if line
                }
                conflicts = files & set(self.unstaged_changes)
                assert not conflicts, "Change in unstaged files, aborting"

                # Strip site name from the start
                files = [fname[len(self.sync.site):] for fname in files]
                # Strip filename to get the object path
                dirs = [fname.rsplit('/', 1)[0] for fname in files]
                # Make unique and sort
                paths = sorted(set(dirs))

                self.sync.playback_paths(
                    paths=paths,
                    recurse=False,
                    override=True,
                    skip_errors=self.args.skip_errors,
                    dryrun=self.args.dry_run,
                )

                if self.args.dry_run:
                    self.abort()
                elif self.unstaged_changes:
                    self.gitcmd_run('stash', 'pop')

            except Exception:
                self.logger.error('Error during operation. Resetting.')

                # Special handling in case of interrupted cherry-pick: show
                # differences in affected files
                cpfname = os.path.join(self.sync.base_dir,
                                       '.git/CHERRY_PICK_HEAD')
                if os.path.exists(cpfname):
                    with open(cpfname) as f:
                        failed_commit = f.read().strip()
                    output = self.gitcmd_output(
                        'diff-tree', '--no-commit-id', '--name-only',
                        '-r', failed_commit,
                    )
                    affected_files = [
                        line
                        for line in output.strip().split('\n')
                        if line
                    ]
                    self.logger.error("The cherry-pick failed due to the"
                                      " following difference:")
                    try:
                        self.gitcmd_run('diff', failed_commit + '~', 'HEAD',
                                        '--', *affected_files)
                    except subprocess.CalledProcessError:
                        # Make sure the call to abort is still done, even if
                        # for example the list of affected_files is too long
                        self.logger.exception("Unable to show diff")

                self.abort()
                raise

        return wrapper

    def create_file(self, file_path, content, binary=False):
        flags = 'wb' if binary else 'w'
        with open(file_path, flags) as create_file:
            create_file.write(content)

    def abort(self):
        '''Abort actions on repo and revert stash. check_repo must be
        called before this can be used'''
        current, branches = self._branch_info()
        # reset currently checked out branch
        target = self.branches.get(current)
        if target is None:
            # The branch was not originally present - we still need to reset it
            # to abort any operation
            target = branches[current]
        self.gitcmd_run('reset', '--hard', target)

        # reset all other branches
        for branch in self.branches:
            if branch == current:
                continue
            if branches[branch] == self.branches[branch]:
                continue
            self.gitcmd_run('branch', '-f', branch, self.branches[branch])

        # check out original branch
        if current != self.orig_branch:
            self.gitcmd_run('checkout', self.orig_branch)

        if self.unstaged_changes:
            self.gitcmd_run('stash', 'pop')

    def run(self):
        '''
        Overwrite for the action that is to be performed if this subcommand is
        chosen.
        '''
        print(self.args)
