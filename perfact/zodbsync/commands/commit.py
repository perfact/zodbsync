#!/usr/bin/python3

from ..subcommand import SubCommand


class Commit(SubCommand):
    """
    Commit changes to given files, possibly prepending a commit that helps in a
    layer setup.
    """

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '-m', type=str, help="Commit message"
        )
        parser.add_argument(
            'path', type=str, nargs='+',
            help="Paths in which changes are to be commited"
        )

    def run(self):
        pass
