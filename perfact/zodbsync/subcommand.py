#!/usr/bin/env python

class SubCommand():
    '''
    Base class for different sub-commands to be used by zodbsync.
    '''
    @classmethod
    def register(cls, subs):
        ''' Add a subparser for myself. '''
        if cls is SubCommand:
            # Only for subclasses
            return
        subparser = subs.add_parser(cls.__name__.lower())
        cls.add_args(subparser)
        subparser.set_defaults(runner=cls)

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
