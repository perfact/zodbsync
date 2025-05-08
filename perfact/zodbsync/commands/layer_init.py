#!/usr/bin/env python

import os
import subprocess as sp

from ..subcommand import SubCommand


class LayerInit(SubCommand):
    """Register layers by copying over the checksum files."""
    subcommand = 'layer-init'

    @staticmethod
    def add_args(parser):
        pass

    def run(self):
        for layer in self.sync.layers:
            ident = layer['ident']
            if not ident:
                continue
            source = layer['source']
            target = layer['base_dir']
            if os.path.isdir(source):
                sp.run(
                    ['rsync', '-a', '--delete-during', f'{source}/__root__/',
                     f'{target}/__root__/'],
                    check=True,
                )
            else:
                # TAR file
                sp.run(
                    ['tar', 'xf', source, '-C', f'{target}/__root__/',
                     '--recursive-unlink'],
                    check=True,
                )
