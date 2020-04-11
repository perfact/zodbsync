#!/usr/bin/env python

from ..subcommand import SubCommand

class Watch(SubCommand):
    ''' Sub-command to start watcher, which periodically records changes as
    they occur.
    '''
    def add_args(self, parser):
        pass

    def run(self, args, sync):
        print(args)

