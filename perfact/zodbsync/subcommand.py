#!/usr/bin/env python

import argparse
import logging
import sys
import subprocess
import os

try:
    import perfact.loggingtools
except ImportError:
    pass

from .zodbsync import ZODBSync


class SubCommand():
    '''
    Base class for different sub-commands to be used by zodbsync.
    '''
    @staticmethod
    def create(argv, subcommands):
        ''' Create a subcommand runner from the given argument list and the
        list of available subcommands.
        '''
        parser = argparse.ArgumentParser(description='''
            Tool to sync objects between a ZODB and a git-controlled folder on
            the file system.
        ''')
        SubCommand.add_generic_args(parser)
        # add all available SubCommand classes as sub-command runners
        subs = parser.add_subparsers()
        for cls in subcommands:
            name = getattr(cls, 'subcommand', cls.__name__.lower())
            subparser = subs.add_parser(name)
            cls.add_args(subparser)
            subparser.set_defaults(runner=cls)

        args = parser.parse_args(argv)

        return args.runner(args=args)

    @staticmethod
    def add_generic_args(parser):
        ''' Add generic arguments not specific to a subcommand.  '''
        default_configfile = '/etc/perfact/modsync/zodb.py'
        parser.add_argument(
            '--config', '-c', type=str,
            help='Path to config (default: %s)' % default_configfile,
            default=default_configfile
        )
        parser.add_argument(
            '--no-lock', action='store_true',
            help='Do not acquire lock. Only use inside a with-lock wrapper.',
        )
        if 'perfact.loggingtools' in sys.modules:
            perfact.loggingtools.addArgs(parser, name='ZODBSync')

    @staticmethod
    def add_args(parser):
        ''' Overwrite to add arguments specific to sub-command. '''
        pass

    def __init__(self, args):
        '''
        Create syncer and store environment into subcommand instance
        '''
        self.args = args
        if 'perfact.loggingtools' in sys.modules:
            logger = perfact.loggingtools.createLogger(
                args=args, name='ZODBSync'
            )
        else:
            logger = logging.getLogger('ZODBSync')
            logger.setLevel(logging.INFO)
            logger.addHandler(logging.StreamHandler())
            logger.propagate = False

        self.logger = logger
        self.sync = ZODBSync(
            conffile=args.config,
            logger=logger,
            connect=getattr(self, "connect", True),
        )
        self.config = self.sync.config

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
        if path.startswith('__root__'):
            filesystem_path = os.path.join(
                self.config["base_dir"],
                path
            )
        else:
            filesystem_path = os.path.join(
                self.config["base_dir"],
                '__root__',
                path
            )

        return data_fs_path, filesystem_path

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
