#!/usr/bin/env python

import subprocess as sp

from ..subcommand import SubCommand


class LayerInit(SubCommand):
    """Register layers from source to work_dir, assuming objects are already in
    the Data.FS, but are now to be provided by a new layer."""
    subcommand = 'layer-init'

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'ident', type=str, nargs='*',
            help='Layer identifier(s). May be * for all',
        )

    @SubCommand.with_lock
    def run(self):
        layers = {layer['ident']: layer
                  for layer in self.sync.layers
                  if layer['ident']}
        idents = self.args.ident
        if idents == ['*']:
            idents = layers.keys()
        for ident in idents:
            assert ident in layers, "Invalid ident"
        for ident in idents:
            layer = layers[ident]
            source = layer['source']
            target = layer['workdir']
            self.unpack_source(source, target)
            sp.run(['git', 'add', '.'], cwd=target)
            sp.run(['git', 'commit', '-m', 'zodbsync layer-init'], cwd=target)
