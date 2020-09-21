#!/usr/bin/env python

from .subcommand import SubCommand

from .commands.record import Record
from .commands.playback import Playback
from .commands.watch import Watch
from .commands.pick import Pick
from .commands.upload import Upload

# Future ideas:
# from .commands.reset import Reset
# from .commands.rebase import Rebase

commands = [Record, Playback, Watch, Pick, Upload]


def create_runner(argv=None):
    '''
    Use provided arguments to create a runner instance for the selected
    subcommand. If no arguments are supplied, sys.argv is used.
    This is separated for backwards compatibility of the script
    perfact-zoperecord.
    '''
    return SubCommand.create(argv=argv, subcommands=commands)


def run():
    '''Entry point for zodbsync.'''
    create_runner().run()
