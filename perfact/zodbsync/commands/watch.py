#!/usr/bin/env python

from ..subcommand import SubCommand
from ..watcher import ZODBSyncWatcher


class Watch(SubCommand):
    ''' Sub-command to start watcher, which periodically records changes as
    they occur.
    '''
    def run(self):
        self.watcher = ZODBSyncWatcher(self.sync, self.config)
        self.watcher.run()
