#!/usr/bin/env python


class SubCommand():
    '''
    Base class for different sub-commands to be used by zodbsync.
    '''
    @staticmethod
    def add_args(parser):
        ''' Overwrite to add arguments specific to sub-command. '''
        pass

    def run(self):
        '''
        Overwrite for the action that is to be performed if this subcommand is
        chosen.
        '''
        print(self.args)
