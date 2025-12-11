#!/usr/bin/env python

import os
import subprocess as sp

from ..subcommand import SubCommand


class LayerUpdate(SubCommand):
    """Update layers."""
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
            '--message', '-m', type=str, default='zodbsync layer-update',
            help="Commit message base",
        )
        parser.add_argument(
            'ident', type=str, nargs='*',
            help='Layer identifier(s). May be * for all',
        )

    def commit_all(self, target, msg):
        """Commit all unstaged changes in target, returning the commit ID or
        None if there is no change."""
        sp.run(['git', 'add', '.'], cwd=target)
        if sp.run(['git', 'commit', '-m', msg], cwd=target).returncode == 0:
            return sp.check_output(['git', 'rev-parse', 'HEAD'],
                                   cwd=target, text=True).strip()

    def run_layer(self, layer):
        """
        For given layer, commit any unstaged changes, update work_dir from
        source, commit that and play back any changes.
        """
        source = layer['source']
        target = layer['workdir']
        msg = self.args.message
        precommit = self.commit_all(target, f'{msg} (pre)')
        self.unpack_source(source, target)
        changes = [
            line[3:] for line in sp.check_output(
                ['git', 'status', '--porcelain', '-u', '--no-renames'],
                cwd=target,
                text=True,
            ).split('\n')
            if line
        ]
        commit = None
        if changes:
            commit = self.commit_all(target, msg)
        self.restore[layer['ident']] = (precommit, commit)
        return {
            os.path.dirname(line[len('__root__'):])
            for line in changes
            if line.startswith('__root__/')
        }

    def restore_layer(self, layer):
        """
        Restore layer for dry-run or in case of failure
        """
        (precommit, commit) = self.restore[layer['ident']]
        target = layer['workdir']
        if commit:
            sp.run(
                ['git', 'reset', '--hard', f'{commit}~'],
                cwd=target, check=True
            )
        if precommit:
            sp.run(
                ['git', '-reset', f'{precommit}~'],
                cwd=target, check=True
            )

    @SubCommand.with_lock
    def run(self):
        "Process given layers"
        self.restore = {}  # Info for restoring for dry-run
        paths = set()
        layers = {layer['ident']: layer
                  for layer in self.sync.layers
                  if layer['ident']}
        idents = self.args.ident
        if idents == ['*']:
            idents = layers.keys()
        for ident in idents:
            assert ident in layers, "Invalid ident"
            paths.update(self.run_layer(layers[ident]))

        if not paths:
            return
        paths = sorted(paths)
        try:
            self._playback_paths(paths)
        except Exception:
            for ident in idents:
                self.restore_layer(layers[ident])
            # if we are not in dryrun we can't be sure we havent already
            # committed some stuff to the data-fs so playback all paths
            if not self.args.dry_run and paths:
                self.sync.playback_paths(
                    paths=paths,
                    recurse=False,
                    override=True,
                    skip_errors=True,
                    dryrun=False,
                )
            raise

        if self.args.dry_run:
            for ident in idents:
                self.restore_layer(layers[ident])
        else:
            self.sync.record(paths, recurse=False, skip_errors=True,
                             ignore_removed=True)
            for path in paths:
                if self.sync.fs_pathinfo(path)['layeridx'] == 0:
                    self.logger.warning(
                        'Conflict with object in custom layer: ' + path
                    )
