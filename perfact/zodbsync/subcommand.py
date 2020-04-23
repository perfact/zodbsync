#!/usr/bin/env python

import argparse
import logging
import sys

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
            subparser = subs.add_parser(cls.__name__.lower())
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
        self.sync = ZODBSync(conffile=args.config, logger=logger)
        self.config = self.sync.config

    def run(self):
        '''
        Overwrite for the action that is to be performed if this subcommand is
        chosen.
        '''
        print(self.args)
