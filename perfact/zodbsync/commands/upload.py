#!/usr/bin/env python

from .pick import Pick
import os
import sys

PY2 = (sys.version_info.major == 2)

FOLDER_TEMPLATE = '''[
    ('props', []),
    ('title', ''),
    ('type', 'Folder'),
]
'''

JS_TEMPLATE = '''[
    ('props',[[('id','content_type'),('type','string'),('value','text/js')]]),
    ('title','content.min.css'),
    ('type','File'),
]'''

CSS_TEMPLATE = '''[
    ('props',[[('id','content_type'),('type','string'),('value','text/css')]]),
    ('title','content.min.css'),
    ('type','File'),
]'''


META_TEMPLATES = {
    'folder': FOLDER_TEMPLATE,
    'js': JS_TEMPLATE,
    'css': CSS_TEMPLATE
}


class Upload(Pick):
    '''Upload a folder structure, e.g. a JS library, to zope Data.fs
    '''

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            'target', type=str,
            help='Path of target library folder',
        )
        parser.add_argument(
            'path', type=str,
            help='Sub-Path in Data.fs to put target folder',
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

    def run(self):
        '''
        Convert target folder into zodbsync compatible struct in repodir
        and upload it
        '''
        self.sync.acquire_lock()
        self.check_repo()

        # conversion loop: iterate over target folder, create folders in
        # repodir and corresponding files
        for cur_dir_path, dirs, files in os.walk(self.args.target):
            # realtive path to be created in repodir
            cur_dir = os.path.relpath(cur_dir_path, self.args.target)

            # repodir folder creation
            new_folder = os.path.join(self.args.path, cur_dir)
            if PY2:
                os.makedirs(new_folder)
            else:
                os.makedirs(new_folder, exist_ok=True)

            # do not forget meta file for folder
            with open(
                os.path.join(new_folder, '__meta__'), 'w'
            ) as fmetafile:
                fmetafile.write(META_TEMPLATES['folder'])

            # now check files inside of folder
            for filename in files:
                file_ending = filename.split('.')[-1]

                # only support css and js files ... for now
                if file_ending not in ['css', 'js']:
                    continue

                # get file content from target file
                with open(
                    os.path.join(cur_dir_path, filename), 'r'
                ) as sourcefile:
                    file_content = sourcefile.read()

                # in repo each file gets its own folder ...
                new_file_folder = os.path.join(
                    new_folder, filename.replace('.', '_')
                )
                os.makedirs(new_file_folder)

                # ... containing __meta__ and __source__ file
                with open(
                    os.path.join(new_file_folder, '__meta__'), 'w'
                ) as metafile:
                    metafile.write(META_TEMPLATES[file_ending])

                with open(
                    os.path.join(
                        new_file_folder, '__source__.' + file_ending
                    ), 'w'
                ) as sourcefile:
                    sourcefile.write(file_content)

        # conversion done, start playback
        try:
            self.sync.playback_paths(
                paths=self.args.path,
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
        finally:
            self.sync.release_lock()