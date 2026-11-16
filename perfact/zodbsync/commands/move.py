#!/usr/bin/env python
import os
import shutil

from Acquisition import aq_base

from ..subcommand import SubCommand
from ..zodbsync import obj_contents


class Move(SubCommand):
    """Move an object's filesystem representation to a target layer"""

    subcommand = "move"

    @staticmethod
    def add_args(parser):
        parser.add_argument("path", type=str, help="Zope path to move")
        parser.add_argument(
            "layer",
            type=str,
            help="Target layer ident (empty string for the fallback layer)",
        )
        parser.add_argument(
            "--no-recurse",
            action="store_true",
            default=False,
            help="Move only the named object, not its descendants",
        )

    def _find_layer_idx(self, ident):
        for idx, layer in enumerate(self.sync.layers):
            if layer["ident"] == ident:
                return idx
        return None

    def _source_ident(self, obj, path):
        """Determine current layer ident of obj."""
        ident = getattr(aq_base(obj), "zodbsync_layer", None)
        if ident is not None:
            return ident
        pathinfo = self.sync.fs_pathinfo(path)
        if pathinfo["layeridx"] is not None:
            return pathinfo["layers"][pathinfo["layeridx"]]["ident"]
        return ""

    @staticmethod
    def _clear_src_attrs(obj, src_ident, tgt_ident, acquired_ident=None):
        """Recursively update zodbsync_layer on descendants.

        Moving from src_ident to tgt_ident. acquired_ident tracks what a child
        with no explicit attr would inherit from its ancestor chain. When that
        equals tgt_ident, deleting the attr is sufficient. When an intermediate
        ancestor has a different explicit layer, deletion would cause wrong
        acquisition, so the attr is set explicitly to tgt_ident instead.
        """
        if acquired_ident is None:
            acquired_ident = tgt_ident
        for item in obj_contents(obj):
            child = getattr(obj, item)
            child_base = aq_base(child)
            child_ident = getattr(child_base, "zodbsync_layer", None)
            if child_ident == src_ident:
                if acquired_ident == tgt_ident:
                    del child_base.zodbsync_layer
                else:
                    child_base.zodbsync_layer = tgt_ident
                child_acquired = tgt_ident
            elif child_ident is not None:
                child_acquired = child_ident
            else:
                child_acquired = acquired_ident
            Move._clear_src_attrs(child, src_ident, tgt_ident, child_acquired)

    @staticmethod
    def _overlap(src_dir, tgt_dir, recurse):
        """Return the relative paths of objects that are defined in both the
        source and the target directory (only the object itself if not
        recursing)."""
        if not recurse:
            names = [""] if os.path.exists(f"{src_dir}/__meta__") else []
        else:
            names = [
                "" if root == src_dir else os.path.relpath(root, src_dir)
                for root, _, files in os.walk(src_dir)
                if "__meta__" in files
            ]
        return sorted(
            name or "."
            for name in names
            if os.path.exists(os.path.join(tgt_dir, name, "__meta__"))
        )

    def _check_no_overlap(self, src_dir, tgt_dir, path, recurse):
        overlap = self._overlap(src_dir, tgt_dir, recurse)
        if overlap:
            raise SystemExit(
                f"Target layer already defines objects below {path!r}, "
                f"refusing to overwrite: {overlap}"
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
            # _getOb does not acquire from parents, unlike getattr
            child = obj._getOb(part, None)
            if child is None:
                raise SystemExit(f"Unknown path: {path!r}")
            obj = child

        src_ident = self._source_ident(obj, path)

        rel_path = os.path.join(self.sync.site, path.lstrip("/"))

        with self.sync.tm:
            if recurse:
                src_idx = self._find_layer_idx(src_ident)
                if src_idx is not None:
                    src_dir = os.path.join(
                        self.sync.layers[src_idx]["workdir"], rel_path
                    )
                    tgt_dir = os.path.join(tgt_workdir, rel_path)
                    if src_dir != tgt_dir and os.path.isdir(src_dir):
                        self._check_no_overlap(src_dir, tgt_dir, path, True)
                        os.makedirs(os.path.dirname(tgt_dir), exist_ok=True)
                        shutil.copytree(src_dir, tgt_dir, dirs_exist_ok=True)
                        shutil.rmtree(src_dir)
                obj.zodbsync_layer = tgt_ident
                self._clear_src_attrs(obj, src_ident, tgt_ident)
            else:
                pathinfo = self.sync.fs_pathinfo(path)
                if pathinfo["fspath"] is not None:
                    src_dir = pathinfo["fspath"]
                    tgt_dir = os.path.join(tgt_workdir, rel_path)
                    if src_dir != tgt_dir:
                        self._check_no_overlap(src_dir, tgt_dir, path, False)
                        os.makedirs(tgt_dir, exist_ok=True)
                        for name in os.listdir(src_dir):
                            if name == "__meta__" or name.startswith("__source"):
                                shutil.copy2(
                                    os.path.join(src_dir, name),
                                    os.path.join(tgt_dir, name),
                                )
                        self.sync.fs_delete_files(src_dir)
                obj.zodbsync_layer = tgt_ident

        self.sync.fs_prune_empty_dirs()
