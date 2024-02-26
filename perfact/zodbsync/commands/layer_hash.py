#!/usr/bin/env python

import os
import hashlib

from ..subcommand import SubCommand


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
        """
        Create a sorted list of hashes for each folder below <path>/__root__.
        This is used when changing the contents of a layer to recognize which
        objects are to be played back.
        For each folder that contains files, it creates a sha1sum over:
        - The sorted list of files
        - The concatenation of the file contents
        The output is written to <path>/.checksums.
        """
        root = os.path.join(self.args.path, '__root__')
        todo = [root]
        with open(os.path.join(self.args.path, '.checksums'), 'w') as fd:
            while todo:
                path = todo.pop()
                entries = list(os.scandir(path))
                todo.extend(sorted((entry.path for entry in entries
                                    if entry.is_dir()), reverse=True))
                files = sorted(entry.path for entry in entries
                               if entry.is_file())
                if not files:
                    continue

                h = hashlib.sha1()
                for file in files:
                    h.update(file.encode('utf-8') + b'\n')
                h.update(b'\n')
                for fname in files:
                    with open(fname, 'rb') as f:
                        while data := f.read(1024*1024):
                            h.update(data)
                print(h.hexdigest(), path[len(root):] or '/', file=fd)
