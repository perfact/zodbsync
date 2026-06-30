#!/usr/bin/env python
import os
import shutil
import subprocess as sp

from Acquisition import aq_base

from ..subcommand import SubCommand
from ..zodbsync import obj_contents


class Copy(SubCommand):
    """Copy an object's filesystem representation to a higher-priority layer.

    The source layer's workdir is reset to its last git-committed state via
    'git checkout HEAD'. If the source has no uncommitted changes, the copy is
    content-identical and the record/watch compression pass will collapse it on
    the next cycle. This is a known limitation.
    """

    subcommand = "copy"

    @staticmethod
    def add_args(parser):
        parser.add_argument("path", type=str, help="Zope path to copy")
        parser.add_argument(
            "layer",
            type=str,
            help="Target layer ident (empty string for the fallback layer)",
        )
        parser.add_argument(
            "--no-recurse",
            action="store_true",
            default=False,
            help="Copy only the named object, not its descendants",
        )

    def _find_layer_idx(self, ident):
        for idx, layer in enumerate(self.sync.layers):
            if layer["ident"] == ident:
                return idx
        return None

    def _source_ident(self, obj, path):
        ident = getattr(aq_base(obj), "zodbsync_layer", None)
        if ident is not None:
            return ident
        pathinfo = self.sync.fs_pathinfo(path)
        if pathinfo["layeridx"] is not None:
            return pathinfo["layers"][pathinfo["layeridx"]]["ident"]
        return ""

    def _copy_obj(
        self,
        obj,
        path,
        src_ident,
        tgt_ident,
        src_workdir,
        tgt_workdir,
        recurse,
        is_root,
        paths_to_restore,
    ):
        obj_base = aq_base(obj)
        pathinfo = self.sync.fs_pathinfo(path)
        if not is_root:
            if pathinfo["layeridx"] is not None:
                child_ident = pathinfo["layers"][pathinfo["layeridx"]]["ident"]
                if child_ident != src_ident:
                    return
        if pathinfo["fspath"] is not None:
            src_dir = pathinfo["fspath"]
            rel_path = os.path.join(self.sync.site, path.lstrip("/"))
            tgt_dir = os.path.join(tgt_workdir, rel_path)
            if src_dir != tgt_dir:
                os.makedirs(tgt_dir, exist_ok=True)
                for name in os.listdir(src_dir):
                    if name == "__meta__" or name.startswith("__source"):
                        shutil.copy2(
                            os.path.join(src_dir, name),
                            os.path.join(tgt_dir, name),
                        )
                        paths_to_restore.append(os.path.join(rel_path, name))

        obj_base.zodbsync_layer = tgt_ident

        if not recurse:
            return

        for item in obj_contents(obj):
            child = getattr(obj, item)
            self._copy_obj(
                child,
                os.path.join(path, item),
                src_ident,
                tgt_ident,
                src_workdir,
                tgt_workdir,
                recurse,
                is_root=False,
                paths_to_restore=paths_to_restore,
            )

    @SubCommand.with_lock
    def run(self):
        path = self.args.path
        tgt_ident = self.args.layer
        recurse = not self.args.no_recurse

        tgt_idx = self._find_layer_idx(tgt_ident)
        if tgt_idx is None:
            raise SystemExit(f"Unknown layer ident: {tgt_ident!r}")
        tgt_workdir = self.sync.layers[tgt_idx]["workdir"]

        obj = self.sync.app
        for part in path.split("/"):
            if not part:
                continue
            obj = getattr(obj, part)

        src_ident = self._source_ident(obj, path)

        src_idx = self._find_layer_idx(src_ident)
        if src_idx is None:
            raise SystemExit(f"Cannot determine source layer for path: {path!r}")
        src_workdir = self.sync.layers[src_idx]["workdir"]

        paths_to_restore = []
        with self.sync.tm:
            self._copy_obj(
                obj,
                path,
                src_ident,
                tgt_ident,
                src_workdir,
                tgt_workdir,
                recurse,
                is_root=True,
                paths_to_restore=paths_to_restore,
            )

        if paths_to_restore:
            sp.run(
                ["git", "checkout", "HEAD", "--"] + paths_to_restore,
                cwd=src_workdir,
                check=True,
            )

        self.sync.fs_prune_empty_dirs()
