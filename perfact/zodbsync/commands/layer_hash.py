#!/usr/bin/env python

import os

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
            'path', type=str, help="Base dir of layer"
        )

    def run(self):
        path = self.args.path
        with open(os.path.join(path, '.checksums'), 'w') as f:
            root = os.path.join(path, '__root__')
            for path, checksum in hashdir(root):
                print(checksum, path, file=f)
