#!/usr/bin/env python

import os
import shutil

from ..subcommand import SubCommand
from ..helpers import path_diff


class LayerUpdate(SubCommand):
    """Update a layer. Check stored checksum file against file in layer and
    playback relevant paths."""
    subcommand = 'layer-update'

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Only check for conflicts and roll back at the end.',
        )
        parser.add_argument(
            '--skip-errors', action='store_true',
            help="Skip failed objects and continue",
            default=False
        )
        parser.add_argument(
            'ident', type=str, nargs='*',
            help='Layer identifier(s)',
        )

    @staticmethod
    def read_checksums(fname):
        result = []
        for line in open(fname):
            line = line.rstrip('\n')
            if line:
                checksum, path = line.split(' ', 1)
                result.append((path, checksum))
        return result

    def run(self):
        paths = set()
        layer_paths = {layer['ident']: layer['base_dir']
                       for layer in self.sync.layers}
        fnames = []
        for ident in self.args.ident:
            assert ident in layer_paths, "Invalid ident"
            fnames.append((
                os.path.join(self.sync.base_dir, '.layer-checksums', ident),
                os.path.join(layer_paths[ident], '.checksums')
            ))
            paths.update(path_diff(
                self.read_checksums(fnames[-1][0]),
                self.read_checksums(fnames[-1][1]),
            ))

        if not paths:
            return
        paths = sorted(paths)
        self._playback_paths(paths)

        if not self.args.dry_run:
            self.sync.record(paths, recurse=False, skip_errors=True,
                             ignore_removed=True)
            for path in paths:
                if self.sync.fs_pathinfo(path)['layeridx'] == 0:
                    self.logger.warning(
                        'Conflict with object in custom layer: ' + path
                    )
            for oldfname, newfname in fnames:
                shutil.copyfile(newfname, oldfname)
