#!/usr/bin/env python

import sys
import argparse
import logging

try:
    import perfact.loggingtools
except ImportError:
    pass

from .zodbsync import ZODBSync

from .commands.record import Record
from .commands.playback import Playback
from .commands.watch import Watch
from .commands.pick import Pick
# Future ideas:
# from .commands.reset import Reset
# from .commands.rebase import Rebase

commands = [Record, Playback, Watch, Pick]


def create_runner(argv=None):
    '''
    Use provided arguments to create a runner instance for the selected
    subcommand. If no arguments are supplied, sys.argv is used.
    This is separated for backwards compatibility of the script
    perfact-zoperecord.
    '''
    parser = argparse.ArgumentParser(description='''
        Tool to sync objects between a ZODB and a git-controlled folder on the
        file system.
    ''')
    default_configfile = '/etc/perfact/modsync/zodb.py'
    parser.add_argument('--config', '-c', type=str,
                        help='Path to config (default: %s)'
                        % default_configfile,
                        default=default_configfile)
    if 'perfact.loggingtools' in sys.modules:
        perfact.loggingtools.addArgs(parser, name='ZODBSync')

    # add all available SubCommand classes as sub-command runners
    subs = parser.add_subparsers()
    for cls in commands:
        subparser = subs.add_parser(cls.__name__.lower())
        cls.add_args(subparser)
        subparser.set_defaults(runner=cls)

    args = parser.parse_args(argv)

    logger = None
    if 'perfact.loggingtools' in sys.modules:
        logger = perfact.loggingtools.createLogger(args=args, name='ZODBSync')
    else:
        logger = logging.getLogger('ZODBSync')
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.StreamHandler())
        logger.propagate = False

    sync = ZODBSync(conffile=args.config, logger=logger)
    # Create runner and insert environment
    runner = args.runner()
    runner.config = sync.config
    runner.args = args
    runner.sync = sync
    runner.logger = logger


def run():
    '''Entry point for zodbsync.'''
    create_runner().run()
