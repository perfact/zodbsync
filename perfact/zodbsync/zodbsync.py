#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import shutil
import time  # for periodic output
import sys
import logging
import subprocess as sp

# for using an explicit transaction manager
import transaction
# for "logging in"
import AccessControl.SecurityManagement
# For config loading and initial connection, possibly populating an empty ZODB
import Zope2.App.startup
import App.config
from Zope2.Startup.run import configure_wsgi

# Plugins for handling different object types
from .object_types import object_handlers, mod_implemented_handlers
from .helpers import StrRepr, to_string, literal_eval, remove_redundant_paths
from .helpers import load_config


# Monkey patch ZRDB not to connect to databases immediately.
try:
    from Shared.DC.ZRDB import Connection
    Connection.Connection.connect_on_load = False
except ImportError:  # pragma: no cover
    pass


def mod_format(data=None):
    '''Make a printable output of the given object data.'''

    # This defines which levels of each key should be split into separate lines
    # if they contain an iterable, in addition to the default rule
    rules = {
        'perms': [4],
        'props': [5],
        'local_roles': [4],
    }

    return StrRepr()(data, rules)


def obj_contents(obj):
    ''' Fetch list of subitems '''
    func = getattr(obj, 'objectIds')
    return sorted(func()) if func else []


def mod_read(obj=None, onerrorstop=False, default_owner=None,
             force_default_owner=False):
    '''Build a consistent metadata dictionary for all types.'''

    # Known types:
    known_types = list(object_handlers.keys())

    # TODO:
    # - Preconditions ?
    # - Site Access Rules ?

    meta = {}

    # The Zope object type is always in the same place

    meta_type = obj.meta_type
    meta['type'] = meta_type

    # The title should always be readable
    title = getattr(obj, 'title', None)
    # see comment in helpers.py:str_repr for why we convert to string
    if isinstance(title, (bytes, str)):
        meta['title'] = to_string(title)

    # Generic and meta type dependent handlers

    if meta_type not in known_types:
        if onerrorstop:
            assert False, "Unsupported type: %s" % meta_type
        else:
            meta['unsupported'] = meta_type
            return meta

    for handler in mod_implemented_handlers(obj, meta_type):
        meta.update(dict(handler.read(obj)))

    # if default owner is set, remove the owner attribute if it matches the
    # default owner. also when force_default_owner is set
    owner_is_default = meta.get('owner') == (['acl_users'], default_owner)
    if (default_owner) and (owner_is_default or force_default_owner):
        if 'owner' in meta:
            del meta['owner']

    meta['zodbsync_layer'] = getattr(obj, 'zodbsync_layer', None)

    return meta


def mod_write(data, parent=None, obj_id=None, override=False, root=None,
              default_owner=None, force_default_owner=False, layer=None):
    '''
    Given object data in <data>, store the object, creating it if it was
    missing. With <override> = True, this method will remove an existing object
    if there is a meta_type mismatch.  If root is given, it should be the
    application root, which is then updated with the metadata in data, ignoring
    parent.
    Returns the existing or created object
    '''

    # Retrieve the object meta type.
    d = dict(data)
    meta_type = d['type']

    no_owner_given = 'owner' not in d

    if (default_owner) and (no_owner_given or force_default_owner):
        d['owner'] = (['acl_users'], default_owner)

    if root is None:
        if hasattr(parent, 'aq_explicit'):
            obj = getattr(parent.aq_explicit, obj_id, None)
        else:
            obj = getattr(parent, obj_id, None)
    else:
        obj = root

    if obj is not None and not hasattr(obj, 'meta_type'):
        logging.getLogger('ZODBSync').warning(
            'Removing property with colliding ID! ({} in {})'.format(
                obj_id, parent
            )
        )
        parent.manage_delProperties(ids=[obj_id])
        obj = None

    temp_obj = None
    # ID exists? Check for type
    if obj and obj.meta_type != meta_type:
        assert override, "Type mismatch for object " + repr(data)
        contents = obj_contents(obj)
        if contents:
            # Rename so we can cut+paste the children
            temp_obj = obj
            temp_id = obj_id
            while temp_id in parent.objectIds():
                temp_id += '_'
            parent.manage_renameObject(obj_id, temp_id)
        else:
            # Remove the existing object in override mode
            parent.manage_delObjects(ids=[obj_id, ])
        obj = None

    # ID is new? Create a minimal object (depending on type)
    if obj is None:
        object_handlers[meta_type].create(parent, data, obj_id)
        if hasattr(parent, 'aq_explicit'):
            obj = getattr(parent.aq_explicit, obj_id, None)
        else:
            obj = getattr(parent, obj_id, None)

    # Send an update (depending on type)
    for handler in mod_implemented_handlers(obj, meta_type):
        handler.write(obj, d)

    # Also write zodbsync layer information
    obj.zodbsync_layer = layer

    if temp_obj:
        children = temp_obj.manage_cutObjects(temp_obj.objectIds())
        obj.manage_pasteObjects(children)
        parent.manage_delObjects([temp_id])

    return obj


class ZODBSync:
    '''A ZODBSync instance is capable of mirroring a part of the ZODB
    object tree in the file system.

    By default, the syncer creates a subdirectory "__root__" in the
    given directory and can use the methods "record()" and
    "playback()" to get all objects from the ZODB or write them back,
    respectively.
    '''

    # We write the binary sources into files ending with appropriate extensions
    # for convenience. This table guesses the most important ones from the
    # "content_type" property.
    content_types = {
        'application/pdf': 'pdf',
        'application/json': 'json',
        'application/javascript': 'js',
        'image/jpeg': 'jpg',
        'image/gif': 'gif',
        'image/png': 'png',
        'text/javascript': 'js',
        'text/css': 'css',
        'text/html': 'html',
        'image/svg+xml': 'svg',
    }

    # In some cases, we can deduce the best extension from the object type.
    meta_types = {
        'Z SQL Method': 'sql',
        'Script (Python)': 'py',
    }

    def __init__(self, config, logger, site='__root__'):
        self.logger = logger
        self.config = config

        self.base_dir = config['base_dir']
        self.site = site
        self.app_dir = os.path.join(self.base_dir, self.site)
        self.manager_user = config.get('manager_user', 'perfact')
        self.default_owner = config.get('default_owner', 'perfact')
        self.force_default_owner = config.get('force_default_owner', False)

        # Statistics
        self.num_obj_total = 1
        self.num_obj_current = 0
        self.num_obj_last_report = time.time()

        # Historically, we switched depending on the given config parameter
        # which configure function to call. However, they essentially do the
        # same and depending on the Zope version, only one is available, so it
        # does not matter how the name of the config was given.
        conf_path = self.config.get('wsgi_conf_path')
        if not conf_path:
            conf_path = self.config.get('conf_path')

        # clear arguments to avoid confusing zope configuration procedure
        sys.argv = sys.argv[:1]

        # Read and parse configuration
        configure_wsgi(conf_path)
        # This initially connects to the ZODB (mostly opening a connection to a
        # running ZEO), sets up the application (which, for Zope 2, includes
        # loading any Products provided in the instance) and, if the ZODB
        # happens to be empty, initializes the application and populates it
        # with some default stuff.
        Zope2.App.startup.startup()

        # We do not use the "global" app object, but open a separate connection
        # with a new transaction manager. This ensures that initializing a
        # second ZODBSync object that connects to a different ZEO in the same
        # thread will not yield "client has seen newer transactions than
        # server!" messages (which is mostly relevant for the tests).
        self.tm = transaction.TransactionManager()
        db = App.config.getConfiguration().dbtab.getDatabase('/', is_root=1)
        root = db.open(self.tm).root
        self.app = root.Application

        # Initialize layers
        layerdir = self.config.get('layers', None)
        layers = []
        fnames = []
        if layerdir and os.path.isdir(layerdir):
            fnames = sorted(os.listdir(layerdir))
        for fname in fnames:
            if any([fname.startswith(key) for key in '.~_']):
                continue
            ident = fname
            if ident.endswith('.py'):
                ident = ident[:-3]
            layer = {
                **{
                    'ident': ident,
                },
                **load_config(f'{layerdir}/{fname}')
            }
            if 'workdir' not in layer or 'source' not in layer:
                raise ValueError(
                    "Old-style layer config without workdir+source"
                )
            layers.append(layer)
            workdir = layer['workdir']
            root = f'{workdir}/{site}'
            if not os.path.isdir(root):
                os.makedirs(root, exist_ok=True)
            if not os.path.isdir(f"{workdir}/.git"):
                sp.run(['git', 'init'], cwd=workdir, check=True)

        # Append default top-level layer
        layers.append({
            'ident': None,
            'workdir': self.config['base_dir'],
        })
        # Reverse order - index zero is the topmost fallback layer
        self.layers = list(reversed(layers))

        # Make sure the manager user exists
        if self.config.get('create_manager_user', False):
            self.create_manager_user()

    def create_manager_user(self):
        """
        Make sure the manager user exists.
        """
        userfolder = getattr(self.app, 'acl_users', None)
        if userfolder is None:
            self.app.manage_addProduct['OFSP'].manage_addUserFolder()
            userfolder = self.app.acl_users
        user = userfolder.getUser(self.manager_user)
        if user is not None:
            return
        self.tm.begin()
        userfolder._doAddUser(self.manager_user, 'admin', ['Manager'], [])
        self.logger.warning(
            'Created user %s with password admin because this user does not'
            ' exist!' % self.manager_user
        )
        self.tm.commit()

    def start_transaction(self, note=''):
        ''' Start a transaction with a given note and return the transaction
        manager, so the caller can call commit() or abort()
        '''
        # Log in as a manager
        uf = self.app.acl_users
        user = uf.getUser(self.manager_user).__of__(uf)
        assert user is not None, (
            'User %s is not available in database. Perhaps you need to set'
            ' create_manager_user in config.py?' % self.manager_user
        )

        self.logger.info('Using user %s' % self.manager_user)
        AccessControl.SecurityManagement.newSecurityManager(None, user)

        self.tm.begin()
        # Set a label for the transaction
        if note:
            self.tm.get().note(note)
        return self.tm

    def source_ext_from_meta(self, meta, obj_id):
        '''Guess a good extension from meta data.'''

        content_type = None

        # Extract meta data from the key-value list passed.
        meta_type = meta.get('type', None)
        props = meta.get('props', [])
        for prop in props:
            d = dict(prop)
            if d['id'] == 'content_type':
                content_type = d['value']
                break

        # txt is the default extension.
        ext = 'txt'
        # If the ID has a period, the extension defaults from the ID.
        if (obj_id or '').find('.') != -1:
            ext = obj_id.rsplit('.', 1)[-1]

        # If there's an extension to use for the object meta_type, use
        # that.
        ext = self.meta_types.get(meta_type, ext)

        # If there's a match in the content_types database, use that.
        ext = self.content_types.get(content_type, ext)
        return ext

    def fs_path(self, path):
        '''
        Return filesystem path corresponding to the object path, which might
        start with a /.
        Note that this is not layer-aware and will always return the path in
        the topmost layer.
        '''
        return os.path.join(self.app_dir, path.lstrip('/'))

    def fs_pathinfo(self, path):
        """
        Find the correct layer for the object with the given Data.FS path.
        The top (custom) layer may have __deleted__ or __frozen__ markers for
        any of the parents of path which disable looking into lower layers.
        We then find the topmost layer that has a __meta__ file for path.

        __deleted__ has the same consequence as __frozen__ here. Only when
        recording objects and possibly compressing the layers is there a
        difference (if an object reappears and was only marked with
        __deleted__, a compression is possible).

        The children are collected from the subfolders of all remaining layers.

        Input:
        :path: a path in the ZODB like /PerFact/test/

        Return value:
        {
            'path': Original argument
            'fspath': Full path on the filesystem in correct layer, None if
                      object is not present.
            'children': List of effective subobjects
            'layers': self.layers or only the topmost layer if lower layers are
                      masked.
            'layeridx': Index in 'layers' where the object's currently defining
                        representation is found.
        }
        """
        layers = self.layers
        check = self.base_dir
        markers = ['__frozen__', '__deleted__']
        for part in [self.site] + path.split('/'):
            if not part:
                continue
            check = os.path.join(check, part)
            if not os.path.isdir(check):
                break
            if any([item in markers for item in os.listdir(check)]):
                # Only keep custom layer
                layers = layers[:1]
                break

        result = {
            'path': path,
            'fspath': None,
            'children': [],
            'layers': layers,
            'layeridx': None,
        }
        path = path.lstrip('/')
        children = set()
        for idx, layer in enumerate(layers):
            fspath = os.path.join(layer['workdir'], self.site, path)
            if not os.path.isdir(fspath):
                continue
            meta = os.path.join(fspath, '__meta__')
            if result['fspath'] is None and os.path.exists(meta):
                result['fspath'] = fspath
                result['layeridx'] = idx

            for entry in os.listdir(fspath):
                if entry in children or entry.startswith('__'):
                    continue
                if os.path.exists(os.path.join(fspath, entry, '__meta__')):
                    children.add(entry)

        result['children'] = sorted(children)
        return result

    def fs_write(self, path, data):
        '''
        Write object data out to a folder with the given path.
        '''
        # If the custom layer has a __deleted__ marker for this object, remove
        # it.
        base_dir = self.fs_path(path)
        delpath = os.path.join(base_dir, '__deleted__')
        if os.path.exists(delpath):
            os.remove(delpath)

        # Find layer that holds the current version of the object, falling back
        # to the custom layer
        pathinfo = self.fs_pathinfo(path)
        base_dir = pathinfo['fspath'] or base_dir

        # Make directory for the object if it's not already there
        if not os.path.isdir(base_dir):
            self.logger.debug("Will create new directory %s" % path)
            os.makedirs(base_dir)
        old_data = self.fs_read(pathinfo['fspath'])

        # Build object
        exclude_keys = ['source', 'zodbsync_layer']
        meta = {
            key: value
            for key, value in data.items()
            if key not in exclude_keys
        }
        fmt = mod_format(meta)
        if isinstance(fmt, str):
            fmt = fmt.encode('utf-8')
        source = data.get('source', None)

        new_data = {'meta': fmt.strip()}
        # Only write out sources if unicode or string
        write_source = isinstance(source, (bytes, str))
        src_fname = None
        if write_source:
            # Write bytes or utf-8 encoded text.
            base = '__source__'
            if isinstance(source, str):
                source = source.encode('utf-8')
                base = '__source-utf8__'

            ext = self.source_ext_from_meta(
                meta=meta,
                obj_id=path.rstrip('/').rsplit('/', 1)[-1],
            )
            src_fname = '{}.{}'.format(base, ext)
            new_data['src_fnames'] = [src_fname]
            new_data['source'] = source

        if old_data != new_data:
            # Path in top layer, might be different than the one where we read
            # the content
            write_base = self.fs_path(path)
            os.makedirs(write_base, exist_ok=True)

            self.logger.debug("Will write %d bytes of metadata" % len(fmt))
            with open(os.path.join(write_base, '__meta__'), 'wb') as f:
                f.write(fmt)

            # Check if there are stray __source* files and remove them first.
            source_files = [s for s in os.listdir(write_base)
                            if s.startswith('__source') and s != src_fname]
            for source_file in source_files:
                os.remove(os.path.join(write_base, source_file))

            if write_source:
                self.logger.debug(
                    "Will write %d bytes of source" % len(source)
                )
                with open(os.path.join(write_base, src_fname), 'wb') as f:
                    f.write(source)

            # We wrote the object to the topmost layer, so the index where the
            # current representation can be found is zero.
            pathinfo['layeridx'] = 0

        # Compress if possible: Compare object with its representation on disk
        # if the current layer is ignored. If it is the same, remove it in the
        # current layer. Continue with the next layer that holds the object
        for idx, layer in enumerate(pathinfo['layers']):
            # This is now the layer that we compare the current layer to in
            # order to check if we can compress it.
            if idx <= pathinfo['layeridx']:
                continue

            fspath = os.path.join(layer['workdir'], self.site,
                                  path.lstrip('/'))
            data = self.fs_read(fspath)
            if not data or not data.get('meta'):
                # No representation on this layer
                continue
            if data != new_data:
                # No compression
                break
            # Remove meta file and all source files
            base = os.path.join(
                pathinfo['layers'][pathinfo['layeridx']]['workdir'],
                self.site, path.lstrip('/')
            )
            os.remove(os.path.join(base, '__meta__'))
            for src in data.get('src_fnames', []):
                os.remove(os.path.join(base, src))
            # Next comparison point
            pathinfo['layeridx'] = idx

        return pathinfo

    def fs_prune(self, pathinfo, contents):
        '''
        Remove all subfolders from path that are not in contents.
        Removes the folder from the top-level directory, but if the effective
        folder that defines the object (in a multi-layer setup) still would
        provide it, recreate the directory and add a __deleted__ file.
        '''
        relpath = os.path.join(self.site, pathinfo['path'].lstrip('/'))
        base_dir = self.fs_path(pathinfo['path'])
        for item in pathinfo['children']:
            if item in contents:
                continue
            tgt = os.path.join(base_dir, item)
            if os.path.isdir(tgt):
                self.logger.info("Removing old item %s from filesystem" % item)
                shutil.rmtree(tgt)
            meta = os.path.join(relpath, item, '__meta__')
            # Omit topmost (custom) layer
            for layer in pathinfo['layers'][1:]:
                if not os.path.exists(os.path.join(layer['workdir'], meta)):
                    continue
                # Mask the path as deleted because it is also present
                # in a lower layer
                os.makedirs(tgt, exist_ok=True)
                with open(os.path.join(tgt, '__deleted__'), 'wb'):
                    pass
                break

    def fs_prune_empty_dirs(self):
        "Remove all empty directories"
        for layer in self.layers:
            start = os.path.join(layer['workdir'], self.site)
            for root, _, _ in os.walk(start, topdown=False):
                if root == start:
                    continue
                if not os.listdir(root):
                    os.rmdir(root)

    def fs_read(self, fspath):
        '''
        Read data from local file system.
        :fspath: is the full filesystem path of the directory.
        Returns a dictionary with
        - the stripped content of the meta file (if there is one)
        - the list of source files if there are any
        - the content of the source file if there is exactly one
        '''
        if fspath is None or not os.path.isdir(fspath):
            return {}

        filenames = os.listdir(fspath)
        if '__meta__' not in filenames:
            return {}

        result = {}
        meta_fname = os.path.join(fspath, '__meta__')
        with open(meta_fname, 'rb') as f:
            result['meta'] = f.read().strip()

        src_fnames = sorted([a for a in filenames if a.startswith('__source')])
        if src_fnames:
            result['src_fnames'] = src_fnames

        if len(src_fnames) == 1:
            with open(os.path.join(fspath, src_fnames[0]), 'rb') as f:
                result['source'] = f.read()
        return result

    def fs_parse(self, fspath, data=None):
        '''
        Parse data obtained from fs_read.
        Returns a dictionary with the parsed data from the
        meta file and an additional "source" key.
        Raises an error if there is no meta file or multiple source files
        '''
        if data is None:
            data = self.fs_read(fspath)

        assert 'meta' in data, 'Missing meta file: ' + fspath
        src_fnames = data.get('src_fnames', [])
        assert len(src_fnames) <= 1, (
            "Multiple source files in " + fspath
        )
        result = dict(literal_eval(data['meta']))
        if src_fnames:
            src_fname = src_fnames[0]
            src = data['source']
            if src_fname.rsplit('.', 1)[0].endswith('-utf8__'):
                src = src.decode('utf-8')
            result['source'] = src

        return result

    def record(self, paths, recurse=True, skip_errors=False,
               ignore_removed=False):
        '''Record Zope objects from the given paths into the local
        filesystem.'''
        # If /a/b as well as /a are to be recorded recursively, drop /a/b
        if recurse:
            remove_redundant_paths(paths)
        for path in paths:
            obj = self.app
            # traverse into the object of interest
            for part in path.split('/'):
                if not part:
                    continue
                if part not in obj.objectIds():
                    # Depending on skip_errors, this yields an error or a
                    # warning later
                    obj = None
                    break
                obj = getattr(obj, part)
            if obj is None and ignore_removed:
                continue
            self.record_obj(obj, path, recurse=recurse,
                            skip_errors=skip_errors)
        self.fs_prune_empty_dirs()

    def record_obj(self, obj, path, recurse=True, skip_errors=False):
        '''Record a Zope object into the local filesystem'''
        try:
            data = mod_read(
                obj,
                default_owner=self.default_owner,
                force_default_owner=self.force_default_owner,
            )
        except Exception:
            severity = 'Skipping' if skip_errors else 'ERROR'
            msg = '{}: Unable to record path {}'.format(severity, path)
            if skip_errors:
                self.logger.warning(msg)
                return
            else:
                self.logger.error(msg)
                raise

        pathinfo = self.fs_write(path, data)
        path_layer = pathinfo['layers'][pathinfo['layeridx']]['ident']

        current_layer = getattr(obj, 'zodbsync_layer', None)
        if current_layer != path_layer:
            with self.tm:
                obj.zodbsync_layer = path_layer

        if not recurse:
            return

        contents = obj_contents(obj) if ('unsupported' not in data) else []
        self.fs_prune(pathinfo, contents)

        # Update statistics
        self.num_obj_total += len(contents)
        now = time.time()
        if now - self.num_obj_last_report > 2:
            self.logger.info('%d obj saved of at least %d, '
                             'current path %s'
                             % (self.num_obj_current,
                                 self.num_obj_total,
                                 path)
                             )
            self.num_obj_last_report = now

        for item in contents:
            self.num_obj_current += 1

            child = getattr(obj, item)
            self.record_obj(
                obj=child,
                path=os.path.join(path, item),
                skip_errors=skip_errors,
            )

    def _playback_path(self, pathinfo):
        '''
        Play back one object from the file system to the ZODB.

        Params:
            :pathinfo: A dict as returned by fs_pathinfo

        Precondition:
            The parent of the object in question must exist. When a parent as
            well as a child is to be removed, `playback_paths` makes sure to
            call the deletion in the correct order.

        Side effects:
            In addition to the effect on the ZODB, it might add elements to
            `self.playback_todo` and/or `self.playback_fixorder`.
        '''
        path = pathinfo['path']
        if self.recurse:
            self.num_obj_current += 1
            now = time.time()
            if now - self.num_obj_last_report > 2:
                self.logger.info(
                    '%d obj checked of at least %d, current path %s'
                    % (self.num_obj_current, self.num_obj_total, path)
                )
                self.num_obj_last_report = now
        else:
            # be more verbose because every path is explicitly requested
            self.logger.info('Uploading %s' % path)

        # fspath is None if the object is to be deleted
        fs_data = pathinfo['fspath'] and self.fs_parse(pathinfo['fspath'])

        # extend fs_data with layerinfo
        if fs_data:
            fs_data['zodbsync_layer'] = pathinfo['layers'][
                pathinfo['layeridx']]['ident']

        # Traverse to the object if it exists
        parent_obj = None
        obj = self.app
        obj_id = None
        obj_path = []
        for part in path.split('/'):
            if not part:
                continue

            if obj is None:
                # Some parent object is missing
                raise ValueError(
                    'Object {} not found when uploading {}'.format(
                        '/'.join(obj_path), path
                    )
                )

            parent_obj = obj
            obj_id = part
            obj_path.append(part)
            if part in obj.objectIds():
                obj = getattr(obj, part)
            else:
                obj = None

            if obj is None and fs_data is None:
                # Obj does not exist, neither on the file system nor in the
                # Data.FS - nothing to do
                return

        if fs_data is None:
            if obj_id == 'acl_users' and path.startswith('/acl_users'):
                return
            self.logger.info('Removing object ' + path)
            try:
                parent_obj.manage_delObjects(ids=[obj_id])
            except AttributeError as e:
                msg = (
                    f'\n\nFailed to remove object {path}, '
                    f'original error was {e}.\n'
                    f'Perhaps your layer workdir is empty?\n'
                    f'Possible solution: Execute layer-init or layer-update.\n'
                )
                raise AssertionError(msg) from e
            return

        if 'unsupported' in fs_data:
            self.logger.warning('Skipping unsupported object ' + path)
            return

        contents = []
        if self.recurse:
            # We remove any to-be-deleted children before updating the object
            # itself, in case a property with the same name is to be created.
            # The addition of new paths is not done here - playback_paths calls
            # them later on.
            contents = pathinfo['children']
            srv_contents = obj_contents(obj) if obj else []

            # Find IDs in Data.fs object not present in file system
            del_ids = [
                a for a in srv_contents
                if a not in contents and
                not (obj == self.app and a == 'acl_users')
            ]
            if del_ids:
                self.logger.warning('Deleting objects ' + repr(del_ids))
                obj.manage_delObjects(ids=del_ids)

        try:
            srv_data = (
                dict(mod_read(
                    obj,
                    default_owner=self.manager_user,
                    force_default_owner=self.force_default_owner,
                ))
                if obj is not None else None
            )
        except Exception:
            self.logger.exception('Unable to read object at %s' % path)
            raise

        if fs_data != srv_data:
            self.logger.debug("Uploading: %s:%s" % (path, fs_data['type']))
            try:
                obj = mod_write(
                    fs_data,
                    parent=parent_obj,
                    obj_id=obj_id,
                    override=self.override,
                    root=(obj if parent_obj is None else None),
                    default_owner=self.default_owner,
                    force_default_owner=self.force_default_owner,
                    layer=pathinfo['layers'][pathinfo['layeridx']]['ident']
                )
            except Exception:
                # If we do not want to get errors from missing
                # ExternalMethods, this can be used to skip them
                severity = 'Skipping' if self.skip_errors else 'ERROR'
                msg = '%s %s:%s' % (severity, path, fs_data['type'])
                if self.skip_errors:
                    self.logger.warning(msg)
                    return
                else:
                    self.logger.error(msg)
                    raise

        self.num_obj_total += len(contents)
        if hasattr(object_handlers[fs_data['type']], 'fix_order'):
            # Store the data for later usage by `_playback_fixorder`.
            self.fs_data[path] = fs_data
            self.playback_fixorder.append(path)

        self.playback_todo.extend([
            self.fs_pathinfo('{}{}/'.format(path, item))
            for item in reversed(contents)
        ])

    def _playback_fixorder(self, path):
        """
        Fix the order of the subobjects of the object found in path.

        Precondition:
            Called by `playback_paths` and is set up by `_playback_path`, which
            also writes the object data that was read from the FS into
            `self.fs_data`.

        Side effects:
            In addition to the effect on the ZODB, it removes the corresponding
            entry from `self.fs_data`.
        """
        obj = self.app
        for part in path.split('/'):
            obj = getattr(obj, part) if part else obj

        fs_data = self.fs_data[path]
        object_handlers[fs_data['type']].fix_order(obj, fs_data)
        del self.fs_data[path]

    def prepare_paths(self, paths):
        # normalize paths - cut off filenames and the site name
        paths = {
            path.rsplit('/', 1)[0] if (
                path.rsplit('/', 1)[-1].startswith('__')
            ) else path
            for path in paths
        }
        paths = sorted({
            path[len(self.site):] if path.startswith(self.site) else path
            for path in paths
        })

        if not len(paths):
            return []

        paths = [path.rstrip('/') + '/' for path in paths]
        return paths

    def playback_paths(self, paths, recurse=True, override=False,
                       skip_errors=False, dryrun=False):
        self.recurse = recurse
        self.override = override
        self.skip_errors = skip_errors
        paths = self.prepare_paths(paths)

        if recurse:
            paths = remove_redundant_paths(paths)

        self.num_obj_current = 0
        self.num_obj_total = len(paths)

        note = 'zodbsync'
        if len(paths) == 1:
            note += ': ' + paths[0]
        txn_mgr = self.start_transaction(note=note)

        # Stack of paths that are to be played back (reversed alphabetical
        # order, we pop elements from the top)
        self.playback_todo = []
        todo = self.playback_todo  # local alias

        # Stack of paths for which we need to fix the order after everything
        # below that path has been handled.
        self.playback_fixorder = []
        fixorder = self.playback_fixorder  # local alias

        # Cached read metadata for paths that are in fixorder so we
        # don't need to read it again from disk.
        self.fs_data = {}

        pathinfo = [self.fs_pathinfo(path) for path in paths]
        # Remove all objects that are to be removed so they do not interfere
        # with properties with the same ID that take their place
        lastdel = None
        for entry in pathinfo:
            if lastdel and entry['path'].startswith(lastdel):
                continue
            if entry['fspath'] is not None:
                todo.append(entry)
                continue
            self._playback_path(entry)
            lastdel = entry['path']
        todo.reverse()

        # Iterate until both stacks are empty. Whenever the topmost element in
        # todo is no longer a subelement of the topmost element of fixorder, we
        # handle the fixorder element, otherwise we handle the todo element.
        try:
            while todo or fixorder:
                if fixorder:
                    # Handle next object on which to fix order unless there are
                    # still subpaths to be handled
                    path = fixorder[-1]
                    if not (todo and todo[-1]['path'].startswith(path)):
                        self._playback_fixorder(fixorder.pop())
                        continue

                entry = todo.pop()
                self._playback_path(entry)
        except Exception:
            self.logger.exception('Error with path: ' + entry['path'])
            txn_mgr.abort()
            raise

        if dryrun:
            self.logger.info('Dry-run. Rolling back')
            txn_mgr.abort()
        else:
            txn_mgr.commit()

    def recent_changes(self, since_secs=None, txnid=None, limit=50,
                       search_limit=100):
        '''Retrieve all distinct paths which have changed recently. Control how
        far to look back in time by supplying the number of seconds in Unix
        time in "since_secs" or the transaction ID at which to stop scanning in
        "txnid". Retrieves at most "limit" distinct paths.
        '''
        paths = []
        newest_txnid = None
        # Clear the request, so we can access undoable_transactions()
        self.app.REQUEST = {}
        # Loop back collecting transactions
        step_size = 10
        cursor = 0
        done = False
        no_records = False
        limit_reached = False
        while cursor < search_limit:
            txns = self.app._p_jar.db().undoInfo(cursor, cursor+step_size)
            if len(txns) == 0 and cursor == 0:
                no_records = True
                break
            for txn in txns:
                if newest_txnid is None:
                    newest_txnid = txn['id']
                if since_secs and txn['time'] < since_secs:
                    done = True
                    break
                if txnid and txn['id'] == txnid:
                    done = True
                    break
                this_path = txn['description'].split('\n')[0]
                # Ignore transaction descriptions not defining a path
                if not this_path.startswith('/'):
                    continue
                # Cut the method which originated the change, leaving
                # only the object.
                this_path = this_path.rsplit('/', 1)[0]
                if this_path not in paths:
                    paths.append(this_path)
                    if len(paths) >= limit:
                        done = True
                        limit_reached = True
                        break
            if done:
                break
            cursor += step_size
        return {
            'paths': paths,
            'newest_txnid': newest_txnid,
            'no_records': no_records,
            'search_limit_reached': not done,
            'limit_reached': limit_reached,
        }

    def txn_write(self, txnid):
        '''Write the newest transaction ID'''
        with open(os.path.join(self.base_dir, '__last_txn__'), 'wb') as f:
            f.write(txnid)

    def txn_read(self):
        '''Read the newest transaction ID'''
        try:
            with open(os.path.join(self.base_dir, '__last_txn__'), 'rb') as f:
                txn = f.read()
        except IOError:
            txn = None
        return txn
