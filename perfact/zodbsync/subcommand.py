#!/usr/bin/env python

import sys
import subprocess
import os
import shutil

import filelock
import json

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
        # use "--no-pager" instead of "-P" for compatibility / readability
        return ['git', '--no-pager', '-C', self.config['base_dir']
                ] + list(args)

    def gitcmd_run(self, *args):
        '''Wrapper to run a git command.'''
        subprocess.check_call(self.gitcmd(*args))

    def gitcmd_try(self, *args):
        '''Wrapper to run a git command, returning return code.'''
        return subprocess.call(self.gitcmd(*args))

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

    @staticmethod
    def unpack_source(src, tgt):
        """
        Unpack files from source to target.
        Each non-hidden folder in src is copied over, each non-hidden file is
        unpacked, both removing superfluous files in the target.
        """
        targetitems = []
        srcitems = os.listdir(src)
        for entry in srcitems:
            if entry.startswith('.'):
                continue
            path = f'{src}/{entry}'
            if os.path.isdir(path):
                # p.e. __root__ or __schema__ as folders
                # Sometimes, there might be some residual folder with .dpkg-new
                # files or similar, even though this is now supplied as file.
                other = [other for other in srcitems
                         if other.startswith(entry) and other != entry]
                if other:
                    continue
                targetitems.append(entry)
                cmd = ['rsync', '-a', '--delete-during',
                       f'{path}/', f'{tgt}/{entry}/']
            else:
                # p.e. __root__.tar.gz -> Unpack to __root__/
                basename = entry.split('.')[0]
                targetitems.append(basename)
                os.makedirs(f'{tgt}/{basename}', exist_ok=True)
                cmd = ['tar', 'xf', path, '-C', f'{tgt}/{basename}/',
                       '--recursive-unlink']
            subprocess.run(cmd, check=True)
        for entry in os.listdir(tgt):
            if entry.startswith('.') or entry in targetitems:
                continue
            shutil.rmtree(f"{tgt}/{entry}")

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

    def _playback_paths(self, paths):
        paths = self.sync.prepare_paths(paths)
        dryrun = self.args.dry_run

        playback_hook = self.config.get('playback_hook', None)
        if playback_hook and os.path.isfile(playback_hook):
            proc = subprocess.Popen(
                playback_hook, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True)
            out, _ = proc.communicate(json.dumps({'paths': paths}))
            returncode = proc.returncode
            if returncode:
                raise AssertionError(
                    "Error calling playback hook, returncode "
                    "{}, [[{}]] on {}".format(
                        returncode, playback_hook, out
                    )
                )
            phases = json.loads(out)
        else:
            phases = [{'name': 'playback', 'paths': paths}]
            if self.config.get('run_after_playback', None):
                phases[-1]['cmd'] = self.config['run_after_playback']

        for ix, phase in enumerate(phases):
            phase_name = phase.get('name') or str(ix)
            phase_cmd = phase.get('cmd')

            self.sync.playback_paths(
                paths=phase['paths'],
                recurse=False,
                override=True,
                skip_errors=self.args.skip_errors,
                dryrun=dryrun,
            )

            if dryrun or not (phase_cmd and os.path.isfile(phase_cmd)):
                continue

            self.logger.info(
                'Calling phase %s, command: %s', phase_name, phase_cmd
            )
            proc = subprocess.Popen(
                phase_cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True)
            out, _ = proc.communicate(json.dumps(
                {'paths': phase['paths']}
            ))
            returncode = proc.returncode

            if returncode:
                self.logger.error(
                    "Error during phase command %s, %s",
                    returncode, out
                )
                if sys.stdin.isatty():
                    print("Enter 'y' to continue, other to rollback")
                    res = input()
                    if res == 'y':
                        continue

                raise AssertionError(
                    "Unrecoverable error in phase command"
                )
            else:
                self.logger.info(out)

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
                self.paths = []
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

                # Make unique and sort
                self.paths = sorted({
                    file for file in files if file.startswith(self.sync.site)
                })

                self._playback_paths(self.paths)

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
                # if we are not in dryrun we can't be sure we havent already
                # committed some stuff to the data-fs so playback all paths
                # abort
                if not self.args.dry_run and self.paths:
                    self.sync.playback_paths(
                        paths=self.paths,
                        recurse=False,
                        override=True,
                        skip_errors=True,
                        dryrun=False,
                    )
                raise

            if not self.args.dry_run:
                is_ancestor = (
                    self.gitcmd_try(
                        "merge-base", "--is-ancestor", self.orig_commit, "HEAD"
                    ) == 0
                )
                if is_ancestor:
                    merge_commits = self.gitcmd_output(
                        "log", "--oneline", "--min-parents=2",
                        f"{self.orig_commit}..HEAD"
                    ).strip()
                    if not merge_commits:
                        head_commit = self.gitcmd_output(
                            "rev-parse", "HEAD"
                        ).strip()
                        self.logger.info(f"{self.orig_commit}..{head_commit}")

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
