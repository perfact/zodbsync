#!/usr/bin/env python

import os
import shutil

from ..subcommand import SubCommand
# from ..helpers import hashdir


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
            old = self.read_checksums(fnames[-1][0])
            new = self.read_checksums(fnames[-1][1])
            oldidx = 0
            newidx = 0
            # Iterate through results, which are ordered by path. Add any
            # deviation to paths
            while oldidx < len(old) or newidx < len(new):
                if old[oldidx] == new[newidx]:
                    oldidx += 1
                    newidx += 1
                    continue
                oldpath = old[oldidx][0]
                newpath = new[newidx][0]
                if oldpath <= newpath:
                    paths.add(oldpath)
                    oldidx += 1
                    continue
                if newpath <= oldpath:
                    paths.add(newpath)
                    newidx += 1

        if not paths:
            return

        self._playback_paths(sorted(paths))

        if not self.args.dry_run:
            for oldfname, newfname in fnames:
                shutil.copyfile(newfname, oldfname)
