#!/usr/bin/env python

from ..subcommand import SubCommand
from ..helpers import hashdir


class LayerHash(SubCommand):
    """Compute hashes for the contents of a layer"""
    subcommand = 'layer-hash'
    connect = False
    use_config = False

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'path', type=str, help="Root folder of layer"
        )

    def run(self):
        for path, checksum in hashdir(self.args.path.rstrip('/')):
            print(checksum, path)
