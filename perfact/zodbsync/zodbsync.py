#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import sys
import os
import ast
import shutil
import time  # for periodic output
import filelock

# for accessing Data.fs directly:
import Zope2
# for making an annotation to the transaction
import transaction
# for "logging in"
import AccessControl.SecurityManagement

# Logging (if perfact.loggingtools is not available, we only support logging to
# stdout)
import logging
# Plugins for handling different object types
from .object_types import object_handlers, mod_implemented_handlers

from .helpers import str_repr, to_string, literal_eval, fix_encoding, \
    remove_redundant_paths

PY2 = (sys.version_info.major == 2)

# Python2 backward compatibility
if PY2:
    import imp  # for config loading
    ast.Bytes = ast.Str

    class DummyNameConstant:
        pass
    ast.NameConstant = DummyNameConstant
else:
    import importlib  # for config loading

# Monkey patch ZRDB not to connect to databases immediately.
try:
    from Shared.DC.ZRDB import Connection
    Connection.Connection.connect_on_load = False
except ImportError:
    pass

if not PY2:
    # for calling isinstance later
    unicode = str


def mod_format(data=None, indent=0, as_list=False):
    '''Make a printable output of the given object data. Indent the lines
    with <indent> spaces. Return a string or a list of lines if
    <as_list> is True.
    '''

    # Convert dictionary to sorted list of tuples (diff-friendly!)
    if isinstance(data, dict):
        data = [(key, value) for key, value in data.items()]
        data.sort()

    # The data is now given by a list of tuples, each of which has two elements
    # (diff-friendly version of a dict). The first element of the tuple is a
    # string, while the second one might be any combination of lists, tuples
    # and PODs (unicode, bytes, numbers, booleans ...). Usually, we keep each
    # element in one line. An exception are lists with multiple elements, which
    # allow an additional indentation, being split over multiple lines.
    output = []
    output.append('[')
    for key, value in data:
        key_repr = '    (%s, ' % str_repr(key)
        if isinstance(value, list) and len(value) > 1:
            # Non-trivial lists are split onto separate lines.
            output.append(key_repr + '[')
            for item in value:
                output.append('        %s,' % str_repr(item))
            output.append('        ]),')
        else:
            output.append(key_repr + '%s),' % str_repr(value))
    output.append(']')

    if as_list:
        return output
    else:
        return '\n'.join(output)


def obj_contents(obj):
    ''' Fetch list of subitems '''
    if not hasattr(obj, 'objectItems'):
        return []
    result = [a[0] for a in obj.objectItems()]
    result.sort()
    return result


def mod_read(obj=None, onerrorstop=False, default_owner=None):
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
    # default owner
    if (default_owner is not None
            and meta.get('owner', None) == (['acl_users'], default_owner)):
        del meta['owner']

    return meta


def mod_write(data, parent=None, obj_id=None, override=False, root=None,
              default_owner=None):
    '''
    Given object data in <data>, store the object, creating it if it was
    missing. With <override> = True, this method will remove an existing object
    if there is a meta_type mismatch.  If root is given, it should be the
    application root, which is then updated with the metadata in data, ignoring
    parent.
    Returns a dict with the following content:
      'obj': the (existing or created) object
      'override': True if it was necessary to override the object
    '''

    result = {'override': False}

    # Retrieve the object meta type.
    d = dict(data)
    meta_type = d['type']

    if default_owner is not None and 'owner' not in d:
        d['owner'] = (['acl_users'], default_owner)

    # ID exists? Check if meta type matches, emit an error if not.

    if root is None:
        if hasattr(parent, 'aq_explicit'):
            obj = getattr(parent.aq_explicit, obj_id, None)
        else:
            obj = getattr(parent, obj_id, None)
    else:
        obj = root

    # ID exists? Check for type
    if obj and obj.meta_type != meta_type:
        if override:
            # Remove the existing object in override mode
            parent.manage_delObjects(ids=[obj_id, ])
            result['override'] = True
            obj = None
        else:
            assert False, "Type mismatch for object " + repr(data)

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

    result['obj'] = obj
    return result


def obj_modtime(obj):
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

    def __init__(self,
                 conffile,
                 site='__root__',
                 logger=None,
                 ):
        if logger is None:
            logger = logging.getLogger('ZODBSync')
            logger.setLevel(logging.INFO)
            logger.addHandler(logging.StreamHandler())
            logger.propagate = False
        self.logger = logger

        # Load configuration
        if PY2:
            config = imp.load_source('config', conffile)
        else:
            config = importlib.machinery.SourceFileLoader(
                'config', conffile).load_module()

        self.config = config
        self.site = site
        self.base_dir = config.base_dir
        self.lock = filelock.FileLock(self.base_dir + '/.zodbsync.lock')
        self.manager_user = getattr(config, 'manager_user', 'perfact')
        self.create_manager_user = getattr(config, 'create_manager_user',
                                           False)
        self.default_owner = getattr(config, 'default_owner', 'perfact')

        # Setup Zope
        if getattr(config, 'conf_path', None):
            # Zope2 uses the system argument list, which confuses things.
            # We clear that list here. If arguments to Zope2 are required,
            # these can be added here.
            sys.argv = sys.argv[:1]
            Zope2.configure(config.conf_path)
        else:
            # WSGI mode
            from Zope2.Startup.run import configure_wsgi
            configure_wsgi(config.wsgi_conf_path)
            from Zope2.App.startup import startup
            startup()
        self.app = Zope2.app()

        # Statistics
        self.num_obj_total = 1
        self.num_obj_current = 0
        self.num_obj_last_report = time.time()

        # Some objects should be ignored by the process because of
        # their specific IDs.
        self.ignore_objects = [re.compile('^__'), ]

        # We write the binary sources into files ending with
        # appropriate extensions for convenience. This table guesses
        # the most important ones from the "content_type" property.
        self.content_types = {
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

        # In some cases, we can deduce the best extension from the
        # object type.
        self.meta_types = {
            'Z SQL Method': 'sql',
            'Script (Python)': 'py',
        }

    def acquire_lock(self, timeout=10):
        try:
            self.lock.acquire(timeout=1)
        except filelock.Timeout:
            self.logger.debug("Acquiring exclusive lock...")
            try:
                self.lock.acquire(timeout=timeout)
            except filelock.Timeout:
                self.logger.error("Unable to acquire lock.")
                sys.exit(1)

    def release_lock(self):
        self.lock.release()

    def start_transaction(self, note=''):
        ''' Start a transaction with a given note and return the transaction
        manager, so the caller can call commit() or abort()
        '''
        # Log in as a manager
        uf = self.app.acl_users
        user = uf.getUserById(self.manager_user)
        if (user is None):
            if (self.create_manager_user):
                user = uf._doAddUser(self.manager_user, 'admin', ['Manager'],
                                     [])
                self.logger.warning('Created user %s with password admin '
                                    'because this user does not exist!' %
                                    self.manager_user)
            else:
                raise Exception('User %s is not available in database. '
                                'Perhaps you need to set create_manager_user '
                                'in config.py?' % self.manager_user)

        self.logger.info('Using user %s' % self.manager_user)
        if not hasattr(user, 'aq_base'):
            user = user.__of__(uf)
        AccessControl.SecurityManagement.newSecurityManager(None, user)

        txn_mgr = transaction  # Zope2.zpublisher_transactions_manager
        txn_mgr.begin()
        # Set a label for the transaction
        transaction.get().note(note)
        return txn_mgr

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
        return os.path.join(self.base_dir, self.site, path.lstrip('/'))

    def fs_write(self, path, data):
        '''
        Write object data out to a file with the given path.
        '''

        base_dir = self.fs_path(path)
        # Read the basic information
        data = dict(data)
        source = data.get('source', None)

        # Only write out sources if unicode or string
        write_source = isinstance(source, (bytes, unicode))

        # Build metadata
        meta = {key: value for key, value in data.items() if key != 'source'}
        fmt = mod_format(meta)
        if isinstance(fmt, unicode):
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

        if old_data != fmt:
            self.logger.debug("Will write %d bytes of metadata" % len(fmt))
            with open(data_fname, 'wb') as f:
                f.write(fmt)

        # Write source
        if write_source:
            # Check if the source has changed!

            # Write bytes or utf-8 encoded text.
            data = source
            base = '__source__'
            if isinstance(data, unicode):
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
        current_contents = os.listdir(base_dir)
        for item in current_contents:
            if self.is_ignored(item):
                continue

            if item not in contents:
                self.logger.info("Removing old item %s from filesystem" %
                                 item)
                shutil.rmtree(os.path.join(base_dir, item))

    def fs_read(self, path, encoding=None):
        '''Read data from local file system.'''

        base_dir = self.fs_path(path)
        filenames = os.listdir(base_dir)
        src_fnames = [a for a in filenames if a.startswith('__source')]
        assert len(src_fnames) <= 1, "Multiple source files in " + path
        src_fname = src_fnames and src_fnames[0] or None

        meta_fname = os.path.join(base_dir, '__meta__')
        if os.path.isfile(meta_fname):
            with open(meta_fname, 'rb') as f:
                meta_str = f.read()
            meta = dict(literal_eval(meta_str))
        else:
            # if a meta file is missing, we assume a dummy folder
            meta = {'title': '', 'type': 'Folder'}

        if src_fname:
            with open(os.path.join(base_dir, src_fname), 'rb') as f:
                src = f.read()
            if src_fname.rsplit('.', 1)[0].endswith('-utf8__'):
                src = src.decode('utf-8')
            meta['source'] = src

        if encoding is not None:
            # Translate file system data
            meta = dict(fix_encoding(meta, encoding))

        return meta

    def fs_contents(self, path):
        '''Read the current contents from the local file system.'''
        filenames = os.listdir(self.fs_path(path))
        contents = [a for a in filenames if not a.startswith('__')]
        contents.sort()
        return contents

    def is_ignored(self, name):
        '''Decide whether the given ID should be ignored.'''
        ignore_found = False
        for ign in self.ignore_objects:
            if ign.match(name):
                ignore_found = True
                break
        return ignore_found

    def record(self, path='/', recurse=True):
        '''Record Zope objects from the given path into the local
        filesystem.'''
        if not path:
            path = '/'
        obj = self.app
        # traverse into the object of interest
        for part in path.split('/'):
            if part:
                obj = getattr(obj, part)
        self.record_obj(obj, path, recurse=recurse)

    def record_obj(self, obj, path, recurse=True):
        '''Record a Zope object into the local filesystem'''

        data = mod_read(obj, default_owner=self.default_owner)
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
            # Check if one of the ignore patterns matches
            if self.is_ignored(item):
                continue

            child = getattr(obj, item)
            self.record_obj(obj=child, path=os.path.join(path, item))

    def playback(self, path=None, recurse=True, override=False,
                 skip_errors=False, encoding=None):
        '''Play back (write) objects from the local filesystem into Zope.'''
        if not recurse:
            # be more verbose because every path is explicitly requested
            self.logger.info('Uploading %s' % path)
        path = path or ''
        parts = [part for part in path.split('/') if part]
        # Due to the necessity of handling old ZODBs where it was possible to
        # have objects with 'get' as ID (which, unfortunately, was used), we
        # need to handle this case here. An object with 'get' as ID will be
        # left alone and can not be played back.
        if 'get' in parts:
            self.logger.warning('Object "get" cannot be uploaded at path %s' %
                                path)
            return

        # Step through the path components as well as the object tree.
        folder = os.path.join(self.base_dir, self.site)
        parent_obj = None
        obj = self.app
        folder_exists = obj_exists = True
        obj_path = '/'

        part = None
        for part in parts:
            # It is OK if folder_exists or obj_exists is unset in the last step
            # (which either means that the object has to be deleted or that it
            # has to be created), but if one is unset before that, this is an
            # error.
            error = "not found when uploading %s" % path
            assert folder_exists, "Folder %s %s" % (folder, error)
            assert obj_exists, "Object %s %s" % (obj_path, error)

            parent_obj = obj
            if part in [a[0] for a in obj.objectItems()]:
                obj = getattr(obj, part)
            else:
                obj_exists = False
                obj = None

            folder += '/' + part
            obj_path += part + '/'
            if not os.path.isdir(folder):
                folder_exists = False

            if not folder_exists and not obj_exists:
                # we want to allow to pass a list of changed objects (p.e.,
                # from git diff-tree), which might mean that if /a as well as
                # /a/b have been deleted, both will be passed as arguments to
                # perfact-zopeplayback. They are sorted, so /a will already
                # have been deleted, which is why the playback of /a/b will
                # find /a neither on the file system nor in the ZODB. We can
                # simply return in this case.
                return

        if not folder_exists:
            self.logger.info('Removing object ' + path)
            parent_obj.manage_delObjects(ids=[part, ])
            return

        fs_data = self.fs_read(path, encoding=encoding)
        if 'unsupported' in fs_data:
            self.logger.warning('Skipping unsupported object ' + path)
            return

        srv_data = (
            dict(mod_read(obj, default_owner=self.manager_user))
            if obj_exists else None
        )

        if fs_data != srv_data:
            self.logger.debug("Uploading: %s:%s" % (path, fs_data['type']))
            try:
                res = mod_write(
                    fs_data,
                    parent=parent_obj,
                    obj_id=part,
                    override=override,
                    root=(obj if parent_obj is None else None),
                    default_owner=self.default_owner
                )
                obj = res['obj']
                # if we were forced to override, we force recursing for this
                # subpath
                if res['override']:
                    recurse = True
            except Exception:
                # If we do not want to get errors from missing
                # ExternalMethods, this can be used to skip them
                severity = 'Skipping' if skip_errors else 'ERROR'
                msg = '%s %s:%s' % (severity, path, fs_data['type'])
                if skip_errors:
                    self.logger.warning(msg)
                    return
                else:
                    self.logger.error(msg)
                    raise

        if recurse:
            contents = self.fs_contents(path)
            srv_contents = obj_contents(obj)

            # Update statistics
            self.num_obj_total += len(contents)
            now = time.time()
            if now - self.num_obj_last_report > 2:
                self.logger.info(
                    '%d obj checked of at least %d, current path %s'
                    % (self.num_obj_current, self.num_obj_total, path)
                )
                self.num_obj_last_report = now

            # Find IDs in Data.fs object not present in file system
            del_ids = [a for a in srv_contents if a not in contents]
            if del_ids:
                self.logger.warning('Deleting objects ' + repr(del_ids))
                obj.manage_delObjects(ids=del_ids)

            for item in contents:
                self.num_obj_current += 1
                if self.is_ignored(item):
                    continue
                self.playback(path=os.path.join(path, item), override=override,
                              encoding=encoding, skip_errors=skip_errors)

        # Allow actions after recursing, like sorting children
        for handler in mod_implemented_handlers(obj, fs_data['type']):
            handler.write_after_recurse_hook(obj, fs_data)

    def playback_paths(self, paths, recurse=True, override=False,
                       skip_errors=False, encoding=None):
        # normalize paths - cut off filenames and the site name (__root__)
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

        if recurse:
            remove_redundant_paths(paths)

        if not len(paths):
            return

        note = 'perfact-zopeplayback'
        if len(paths) == 1:
            note += ': ' + paths[0]
        txn_mgr = self.start_transaction(note=note)

        try:
            for path in paths:
                self.playback(
                    path=path,
                    override=override,
                    recurse=recurse,
                    skip_errors=skip_errors,
                    encoding=encoding,
                )
        except Exception:
            self.logger.exception('Error with path: ' + path)
            txn_mgr.abort()
            raise
        finally:
            txn_mgr.commit()

    def recent_changes(self, since_secs=None, txnid=None, limit=50,
                       search_limit=100):
        '''Retrieve all distinct paths which have changed recently.  Control
        how far to look back in time by supplying the number of
        seconds in Unix time in "since_secs" or the transaction ID at
        which to stop scanning in "txnid".
        Retrieves at most "limit" distinct paths.
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
