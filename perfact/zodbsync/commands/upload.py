#!/usr/bin/env python

import os
import sys
import warnings

from ..subcommand import SubCommand
from ..zodbsync import mod_format


PY2 = (sys.version_info.major == 2)


def create_template(type, content_type=None):
    result = {'type': type, 'title': ''}
    if content_type is not None:
        result['props'] = [[
                ('id', 'content_type'),
                ('type', 'string'),
                ('value', content_type)
        ]]
    return result


META_TEMPLATES = {
    'folder': create_template('Folder'),
    'js': create_template('File', 'application/javascript'),
    'css': create_template('File', 'text/css'),
    'html': create_template('File', 'text/html'),
    'odt': create_template('File', 'application/vnd.oasis.opendocument.text'),
    'ods': create_template(
        'File', 'application/vnd.oasis.opendocument.spreadsheet'),
    'pdf': create_template('File', 'application/pdf'),
    'svg': create_template('File', 'image/svg+xml'),
}
META_DEFAULT = create_template('File', 'application/octet-stream')


class Upload(SubCommand):
    '''[DEPRECATED] Upload a folder structure, e.g. a JS library, to zope
    Data.FS'''

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'source', type=str,
            help='Path of source library folder',
        )
        parser.add_argument(
            'path', type=str,
            help='Sub-Path in Data.fs to put source folder',
        )
        parser.add_argument(
            '--override', '-o', action='store_true',
            help='Override object type changes when uploading',
            default=False
        )
        parser.add_argument(
            '--skip-errors', action='store_true',
            help="Skip failed objects and continue",
            default=False
        )
        parser.add_argument(
            '--dry-run', action='store_true', default=False,
            help='Roll back at the end.',
        )
        parser.add_argument(
            '--replace-periods', action='store_true', default=False,
            help='Replace periods in file names with underscores',
        )
        parser.add_argument(
            '--valid-extensions', type=str,
            help=(
                'Only upload files with the extensions listed '
                '(comma separated list). Allow all extensions by default.'
            )
        )

    @SubCommand.with_lock
    def run(self):
        '''
        Convert source folder into zodbsync compatible struct in repodir
        and upload it.
        '''
        warnings.warn(
            'Static external assets should not be included in the Data.FS',
            DeprecationWarning
        )
        self.check_repo()

        # we need both filesystem and Data.fs path representation
        data_fs_path, filesystem_path = self.datafs_filesystem_path(
            self.args.path
        )

        valid_extensions = self.args.valid_extensions
        if valid_extensions:
            # Parse comma separated list
            valid_extensions = [
                item.strip()
                for item in valid_extensions.split(',')
                if len(item.strip()) > 0
            ]

        # conversion loop: iterate over source folder, create folders in
        # repodir and corresponding files
        for cur_dir_path, dirs, files in os.walk(self.args.source):
            # relative path to be created in repodir
            cur_dir = os.path.relpath(cur_dir_path, self.args.source)

            # repodir folder creation
            new_folder = os.path.join(filesystem_path, cur_dir)
            if PY2:
                os.makedirs(new_folder)
            else:
                os.makedirs(new_folder, exist_ok=True)

            # do not forget meta file for folder
            self.create_file(
                file_path=os.path.join(new_folder, '__meta__'),
                content=mod_format(META_TEMPLATES['folder'])
            )

            # now check files inside of folder
            for filename in files:
                file_ending = filename.split('.')[-1]

                # bail out if not a valid extension
                if (valid_extensions is not None and
                        file_ending not in valid_extensions):
                    continue

                # read file content from source file
                with open(
                            os.path.join(cur_dir_path, filename), 'rb'
                        ) as sourcefile:
                    file_content = sourcefile.read()

                # choose the original filename, or replace periods
                if self.args.replace_periods:
                    repo_filename = filename.replace('.', '_')
                else:
                    repo_filename = filename

                # in repo each file gets its own folder ...
                new_file_folder = os.path.join(
                    new_folder, repo_filename
                )
                os.makedirs(new_file_folder)

                # ... containing __meta__ and __source__ file
                self.create_file(
                    file_path=os.path.join(new_file_folder, '__meta__'),
                    content=mod_format(
                        META_TEMPLATES.get(file_ending, META_DEFAULT)
                    )
                )
                self.create_file(
                    file_path=os.path.join(
                        new_file_folder, '__source__.' + file_ending
                    ),
                    content=file_content,
                    binary=True
                )

        # conversion done, start playback
        try:
            self.sync.playback_paths(
                paths=[data_fs_path],
                recurse=True,
                override=self.args.override,
                skip_errors=self.args.skip_errors,
                dryrun=self.args.dry_run,
            )

            if self.args.dry_run:
                self.abort()

        except Exception:
            self.logger.exception('Error uploading files. Resetting.')
            self.abort()
            raise
