#!/usr/bin/env python

import os
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
            for entry in os.listdir(source):
                if entry.startswith('.'):
                    continue
                srcentrypath = f'{source}/{entry}'
                if os.path.isdir(srcentrypath):
                    # p.e. __root__ or __schema__ as folders
                    cmd = ['rsync', '-a', '--delete-during',
                           f'{srcentrypath}/', f'{target}/{entry}/']
                else:
                    # p.e. __root__.tar.gz -> Unpack to __root__/
                    basename = entry.split('.')[0]
                    cmd = ['tar', 'xf', srcentrypath, '-C',
                           f'{target}/{basename}/', '--recursive-unlink']
                sp.run(cmd, check=True)
            sp.run(['git', 'add', '.'], cwd=target)
            sp.run(['git', 'commit', '-m', 'zodbsync layer-init'], cwd=target)
