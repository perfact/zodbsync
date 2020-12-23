#!/usr/bin/env python

import os
import sys
import argparse
import logging

import filelock

try:
    import perfact.loggingtools
except ImportError:
    pass

from .helpers import load_config
from .zodbsync import ZODBSync

from .commands.record import Record
from .commands.playback import Playback
from .commands.watch import Watch
from .commands.pick import Pick
from .commands.upload import Upload
from .commands.with_lock import WithLock

# Future ideas:
# from .commands.reset import Reset
# from .commands.rebase import Rebase


class Runner(object):
    """
    Parses arguments to select the correct SubCommand subclass.
    """
    commands = [Record, Playback, Watch, Pick, Upload, WithLock]

    def __init__(self):
        """
        Set up the argument parser with the possible subcommands
        """
        parser = argparse.ArgumentParser(description='''
            Tool to sync objects between a ZODB and a git-controlled folder on
            the file system.
        ''')
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

        # Add all available SubCommand classes as sub-command runners, using
        # either the property "subcommand" or the name of the class.
        # The chosen subcommand class will be available as args.command
        subs = parser.add_subparsers()
        for cls in self.commands:
            name = getattr(cls, 'subcommand', cls.__name__.lower())
            subparser = subs.add_parser(name)
            cls.add_args(subparser)
            subparser.set_defaults(command=cls)

        self.parser = parser

        # These are set by parse()
        self.args = None
        self.logger = None
        self.config = None
        self.lock = None
        self.sync = None
        self.command = None

    def parse(self, *argv):
        """
        Parse the given arguments and set the command accordingly. If no
        arguments are given, sys.argv is used.
        """
        args = self.parser.parse_args(argv if argv else None)
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
        self.config = load_config(args.config)

        # Usually, each command needs a connection to the ZODB, but it might
        # explicitly disable it.
        if getattr(args.command, 'connect', True):
            self.sync = ZODBSync(config=self.config, logger=logger)

        if not args.no_lock:
            self.lock = filelock.FileLock(
                os.path.join(self.config['base_dir'], '.zodbsync.lock')
            )

        self.command = args.command(
            args=self.args,
            logger=self.logger,
            config=self.config,
            sync=self.sync,
            lock=self.lock,
        )

        return self.command

    def run(self, *argv):
        """
        Parse arguments and run command
        """
        self.parse(*argv).run()
