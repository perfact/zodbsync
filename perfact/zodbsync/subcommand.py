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
        return ['git', '-C', self.sync.base_dir] + list(args)

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

    @staticmethod
    def gitop(func):
        """
        - Stash unstaged changes away
        - memorize the current commit
        - do something
        - check for conflicts
        - play back changed objects
        - unstash
        """
        def wrapper(self, *args, **kwargs):
            # Check for unstaged changes
            self.check_repo()

            try:
                func(self, *args, **kwargs)
                # Are these all?
                for fname in ['rebase-merge', 'rebase-apply',
                              'CHERRY_PICK_HEAD', 'MERGE_HEAD', 'REVERT_HEAD']:
                    path = os.path.join(self.sync.base_dir, '.git', fname)
                    assert not os.path.exists(path), "Git state not clean"

                files = {
                    line for line in self.gitcmd_output(
                        'diff', self.orig_commit, '--name-only'
                    ).strip().split('\n')
                    if line
                }
                conflicts = files & set(self.unstaged_changes)
                assert not conflicts, "Change in unstaged files, aborting"

                paths = sorted({
                    filename[len(self.sync.site):].rsplit('/', 1)[0]
                    for filename in files
                })

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
                self.logger.exception('Error during operation. Resetting.')
                self.abort()
                raise

        return wrapper

    def create_file(self, file_path, content):
        with open(file_path, 'w') as create_file:
            create_file.write(content)

    def abort(self):
        '''Abort actions on repo and revert stash. check_repo must be
        called before this can be used'''
        self.gitcmd_run('reset', '--hard', self.orig_commit)
        if self.unstaged_changes:
            self.gitcmd_run('stash', 'pop')

    def run(self):
        '''
        Overwrite for the action that is to be performed if this subcommand is
        chosen.
        '''
        print(self.args)
