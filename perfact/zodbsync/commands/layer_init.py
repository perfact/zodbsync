#!/usr/bin/env python

import os
import shutil

from ..subcommand import SubCommand


class LayerInit(SubCommand):
    """Register layers by copying over the checksum files."""
    subcommand = 'layer-init'

    @staticmethod
    def add_args(parser):
        pass

    def run(self):
        tgt = os.path.join(self.sync.base_dir, '.layer-checksums')
        if not os.path.isdir(tgt):
            os.mkdir(tgt)

        for layer in self.sync.layers:
            ident = layer['ident']
            if not ident:
                continue
            src = os.path.join(layer['base_dir'], '.checksums')
            if os.path.exists(src):
                shutil.copy(src, os.path.join(tgt, ident))
