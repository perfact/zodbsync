#!/usr/bin/env python

class SubCommand():
    '''
    Base class for different sub-commands to be used by zodbsync.
    '''
    def register(self, subs):
        ''' Add a subparser for myself. '''
        if self.__class__ is SubCommand:
            # Only for subclasses
            return
        subparser = subs.add_parser(self.__class__.__name__.lower())
        self.add_args(subparser)
        subparser.set_defaults(runner=self.run)

    def add_args(self, parser):
        ''' Overwrite to add arguments specific to sub-command. '''
        pass

    def run(self, args, sync):
        '''
        Overwrite for the action that is to be performed if this subcommand is
        chosen.
        '''
        print(args)
