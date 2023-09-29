#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import six
import shutil
import time  # for periodic output
import sys
import json
import subprocess

# for using an explicit transaction manager
import transaction
# for "logging in"
import AccessControl.SecurityManagement
# For config loading and initial connection, possibly populating an empty ZODB
import Zope2.App.startup
import App.config
try:
    from Zope2.Startup.run import configure_wsgi as configure_zope
except ImportError:  # pragma: nocover_py3
    from Zope2.Startup.run import configure as configure_zope

# Plugins for handling different object types
from .object_types import object_handlers, mod_implemented_handlers
from .helpers import StrRepr, to_string, literal_eval, fix_encoding, \
    remove_redundant_paths


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
    if isinstance(title, (six.binary_type, six.text_type)):
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

    return meta


def mod_write(data, parent=None, obj_id=None, override=False, root=None,
              default_owner=None, force_default_owner=False):
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

    if temp_obj:
        children = temp_obj.manage_cutObjects(temp_obj.objectIds())
        obj.manage_pasteObjects(children)
        parent.manage_delObjects([temp_id])

    return obj


def obj_modtime(obj):  # pragma: no cover
    '''
    Allow access to private method of an object to read out the modtime.
    '''
    return obj._p_mtime


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
        configure_zope(conf_path)
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
        '''
        return os.path.join(self.app_dir, path.lstrip('/'))

    def fs_write(self, path, data):
        '''
        Write object data out to a file with the given path.
        '''

        base_dir = self.fs_path(path)
        # Read the basic information
        data = dict(data)
        source = data.get('source', None)

        # Only write out sources if unicode or string
        write_source = isinstance(source, (bytes, six.text_type))

        # Build metadata
        meta = {key: value for key, value in data.items() if key != 'source'}
        fmt = mod_format(meta)
        if isinstance(fmt, six.text_type):
            fmt = fmt.encode('utf-8')

        # Make directory for the object if it's not already there
        try:
            os.stat(base_dir)
        except OSError:
            self.logger.debug("Will create new directory %s" % path)
            os.makedirs(base_dir)

        # Metadata
        data_fname = os.path.join(base_dir, '__meta__')
        # Check if data has changed!
        try:
            with open(data_fname, 'rb') as f:
                old_data = f.read()
        except IOError:
            old_data = None

        if old_data is None or old_data.strip() != fmt.strip():
            self.logger.debug("Will write %d bytes of metadata" % len(fmt))
            with open(data_fname, 'wb') as f:
                f.write(fmt)

        # Write source
        if write_source:
            # Check if the source has changed!

            # Write bytes or utf-8 encoded text.
            data = source
            base = '__source__'
            if isinstance(data, six.text_type):
                data = data.encode('utf-8')
                base = '__source-utf8__'

            path = path.rstrip('/')
            ext = self.source_ext_from_meta(
                meta=meta,
                obj_id=os.path.basename(path)
            )
            src_fname = os.path.join(base_dir, '%s.%s' % (base, ext))
        else:
            src_fname = ''

        # Check if there are stray __source* files and remove them first.
        source_files = [s for s in os.listdir(base_dir)
                        if s.startswith('__source') and s != src_fname]
        for source_file in source_files:
            os.remove(os.path.join(base_dir, source_file))

        if write_source:
            # Check if content has changed!
            try:
                with open(src_fname, 'rb') as f:
                    old_data = f.read()
            except IOError:
                old_data = None

            if old_data != data:
                self.logger.debug("Will write %d bytes of source" % len(data))
                with open(src_fname, 'wb') as f:
                    f.write(data)

    def fs_prune(self, path, contents):
        '''
        Remove all subfolders from path that are not in contents
        '''
        base_dir = self.fs_path(path)
        for item in self.fs_contents(path):
            if item not in contents:
                self.logger.info("Removing old item %s from filesystem" %
                                 item)
                shutil.rmtree(os.path.join(base_dir, item))

    def fs_read(self, path):
        '''Read data from local file system.'''

        base_dir = self.fs_path(path)
        if not os.path.isdir(base_dir):
            return None
        filenames = os.listdir(base_dir)
        src_fnames = [a for a in filenames if a.startswith('__source')]
        assert len(src_fnames) <= 1, "Multiple source files in " + path
        src_fname = src_fnames and src_fnames[0] or None

        meta_fname = os.path.join(base_dir, '__meta__')
        assert os.path.isfile(meta_fname), 'Missing meta file: %s' % meta_fname

        with open(meta_fname, 'rb') as f:
            meta_str = f.read()
        meta = dict(literal_eval(meta_str))

        if src_fname:
            with open(os.path.join(base_dir, src_fname), 'rb') as f:
                src = f.read()
            if src_fname.rsplit('.', 1)[0].endswith('-utf8__'):
                src = src.decode('utf-8')
            meta['source'] = src

        if self.encoding is not None:
            # Translate file system data
            meta = fix_encoding(meta, self.encoding)

        return meta

    def fs_contents(self, path):
        '''Read the current contents from the local file system.'''
        filenames = os.listdir(self.fs_path(path))
        return sorted([f for f in filenames if not f.startswith('__')])

    def record(self, path='/', recurse=True, skip_errors=False):
        '''Record Zope objects from the given path into the local
        filesystem.'''
        if not path:
            path = '/'
        obj = self.app
        # traverse into the object of interest
        for part in path.split('/'):
            if part:
                obj = getattr(obj, part)
        self.record_obj(obj, path, recurse=recurse, skip_errors=skip_errors)

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
            msg = '%s %s' % (severity, path)
            if skip_errors:
                self.logger.warning(msg)
                return
            else:
                self.logger.error(msg)
                raise

        self.fs_write(path, data)

        if not recurse:
            return

        contents = obj_contents(obj) if ('unsupported' not in data) else []
        self.fs_prune(path, contents)

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
            self.record_obj(obj=child, path=os.path.join(path, item),
                            skip_errors=skip_errors)

    def _playback_path(self, path):
        '''
        Play back one object from the file system to the ZODB.

        Params:
            :path: is the path in the ZODB, starting and ending with /

        Precondition:
            The parent of the object in question must exist. When a parent as
            well as a child is to be removed, `playback_paths` makes sure to
            call the deletion in the correct order.

        Side effects:
            In addition to the effect on the ZODB, it might add elements to
            `self.playback_todo` and/or `self.playback_fixorder`.
        '''
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

        # Returns None if the object does not exist in the file system, i.e. it
        # is to be deleted.
        fs_data = self.fs_read(path)

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
            self.logger.info('Removing object ' + path)
            parent_obj.manage_delObjects(ids=[obj_id])
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
            contents = self.fs_contents(path)
            srv_contents = obj_contents(obj) if obj else []

            # Find IDs in Data.fs object not present in file system
            del_ids = [a for a in srv_contents if a not in contents]
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
            '{}{}/'.format(path, item)
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

    def playback_paths(self, paths, recurse=True, override=False,
                       skip_errors=False, encoding=None, dryrun=False):
        self.recurse = recurse
        self.override = override
        self.skip_errors = skip_errors
        self.encoding = encoding
        # normalize paths - cut off filenames and the site name
        paths = {
            path.rsplit('/', 1)[0] if (
                path.endswith('__meta__')
                or path.rsplit('/', 1)[-1].startswith('__source')
            ) else path
            for path in paths
        }
        paths = sorted({
            path[len(self.site):] if path.startswith(self.site) else path
            for path in paths
        })

        if not len(paths):
            return

        paths = [path.rstrip('/') + '/' for path in paths]

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

        # Reverse order, ensure that paths end in '/' so startswith can be used
        # reliably and remove elements that are to be deleted in that reverse
        # order so properties of their parents can take their place
        for path in reversed(paths):
            if not os.path.isdir(self.fs_path(path)):
                # Call it immediately, it will read None from the FS and remove
                # the object
                self._playback_path(path)
            else:
                todo.append(path)

        # Iterate until both stacks are empty. Whenever the topmost element in
        # todo is no longer a subelement of the topmost element of fixorder, we
        # handle the fixorder element, otherwise we handle the todo element.
        try:
            while todo or fixorder:
                if fixorder:
                    # Handle next object on which to fix order unless there are
                    # still subpaths to be handled
                    path = fixorder[-1]
                    if not (todo and todo[-1].startswith(path)):
                        self._playback_fixorder(fixorder.pop())
                        continue

                path = todo.pop()
                self._playback_path(path)
        except Exception:
            self.logger.exception('Error with path: ' + path)
            txn_mgr.abort()
            raise

        if dryrun:
            self.logger.info('Dry-run. Rolling back')
            txn_mgr.abort()
        else:
            txn_mgr.commit()
            postproc = self.config.get('run_after_playback', None)
            if postproc and os.path.isfile(postproc):
                self.logger.info('Calling postprocessing script ' + postproc)
                proc = subprocess.Popen(postproc, stdin=subprocess.PIPE,
                                        universal_newlines=True)
                proc.communicate(json.dumps({'paths': paths}))

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
