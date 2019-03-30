#!/usr/bin/env python

import re
import sys
import os
import string
import ast
import operator
import shutil
import time  # for periodic output

# for accessing Data.fs directly:
import Zope2
# for making an annotation to the transaction
import transaction
# for "logging in"
import AccessControl.SecurityManagement

# Logging
import perfact.zodbsync.logger
# Plugins for handling different object types
from perfact.zodbsync.object_types import object_handlers

PY2 = (sys.version_info.major == 2)

# Python2 backward compatibility
if PY2:
    import imp # for config loading
    ast.Bytes = ast.Str

    class DummyNameConstant:
        pass
    ast.NameConstant = DummyNameConstant
else:
    import importlib # for config loading

# Monkey patch ZRDB not to connect to databases immediately.
from Shared.DC.ZRDB import Connection
Connection.Connection.connect_on_load = False

if not PY2:
    # for calling isinstance later
    unicode = str


def mod_format(data=None, indent=0, as_list=False):
    '''Make a printable output of the given object data. Indent the lines
    with <indent> spaces. Return a string or a list of lines if
    <as_list> is True.
    '''

    def str_repr(val):
        '''Generic string representation of a value.'''
        return str((val,))[1:-2]

    def split_longlines(lines, maxlen=100, threshold=140):
        '''Split a list of strings into a longer list of strings, but each
        with lines no longer than <threshold>, split at <maxlen>.'''
        index = 0
        while True:
            if len(lines[index]) > threshold:
                remainder = lines[index][maxlen:]
                lines[index] = lines[index][:maxlen]
                lines.insert(index+1, remainder)
            index += 1
            if index == len(lines):
                break
        return lines

    output = []

    def make_line(line):
        output.append(indent * ' ' + line)

    make_line('[')
    indent += 4
    for item in data:
        if isinstance(item[1], list):
            # Non-trivial lists are shown on separate lines.
            lines = item[1]
            if len(lines) > 1:
                make_line("("+str_repr(item[0])+", [")
                indent += 4
                for l in lines:
                    make_line(str_repr(l) + ',')
                make_line("]),")
                indent -= 4
            else:
                make_line(str(item)+',')

        elif isinstance(item[1], (bytes, unicode)):
            # Multiline presentation of non-trivial text / blobs
            text = item[1]
            if isinstance(text, bytes):
                newline = b'\n'
            else:
                newline = u'\n'
            if text != '' and (text.find(newline) != -1 or len(text) > 80):
                # Keep newlines after splitting.
                lines = conserv_split(text, newline)
                # Could be binary data. So, split superlong lines as well.
                lines = split_longlines(lines)

                make_line("("+str_repr(item[0])+", ")
                indent += 4
                for l in lines:
                    make_line(str_repr(l) + '+')
                make_line("''),")
                indent -= 4
            else:
                make_line(str(item)+',')
        else:
            make_line(str(item)+',')
    indent -= 4
    make_line(']')

    if as_list:
        return output
    else:
        return '\n'.join(output)

def mod_implemented_handlers(obj, meta_type):
    known_types = list(object_handlers.keys())
    interfaces = ['Properties', 'AccessControl', 'ZCacheable', ]
    interfaces.append(meta_type)
    # return all object handlers for interfaces the object implements
    handlers = [object_handlers[i] for i in interfaces]
    return [h for h in handlers if h.implements(obj)]



def mod_read(obj=None, onerrorstop=False, default_owner=None):
    '''Build a consistent metadata dictionary for all types.'''

    # Known types:
    known_types = list(object_handlers.keys())

    # TODO:
    # - Preconditions ?
    # - Site Access Rules ?

    meta = []

    # The Zope object type is always in the same place

    meta_type = obj.meta_type
    meta.append(('type', meta_type))

    # The title should always be readable
    title = getattr(obj, 'title', None)
    meta.append(('title', title))

    # Generic and meta type dependent handlers

    if meta_type not in known_types:
        if onerrorstop:
            assert False, "Unsupported type: %s" % meta_type
        else:
            additions = [('unsupported', meta_type), ]
            meta.extend(additions)
            meta.sort()
            return meta

    for handler in mod_implemented_handlers(obj, meta_type):
        meta.extend(handler.read(obj))

    # Hash friendly, sorted list of tuples.
    meta.sort()

    # if default owner is set, remove the owner attribute if it matches the
    # default owner
    if default_owner is not None:
        for i in range(len(meta)):
            if meta[i][0] == 'owner':
                if meta[i][1] == (['acl_users'], default_owner):
                    del meta[i]
                break

    return meta


def mod_write(data, parent=None, obj_id=None, override=False, root=None,
              default_owner=None):
    '''
    Given object data in <data>, store the object, creating it if it was
    missing. With <override> = True, this method will remove an existing object
    if there is a meta_type mismatch.  If root is given, it should be the
    application root, which is then updated with the metadata in data, ignoring
    parent.
    Returns the (existing or created) object.
    '''

    # Retrieve the object ID and meta type.

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
            obj = None
        else:
            assert False, "Type mismatch for object " + repr(data)

    # ID is new? Create a minimal object (depending on type)

    if obj is None:
        data['id'] = obj_id
        object_handlers[meta_type].create(parent, data)
        del data['id']
        if hasattr(parent, 'aq_explicit'):
            obj = getattr(parent.aq_explicit, obj_id, None)
        else:
            obj = getattr(parent, obj_id, None)

    # Send an update (depending on type)
    for handler in mod_implemented_handlers(obj, meta_type):
        handler.write(obj, data)

    return obj


def fix_encoding(data, encoding):
    '''Assume that strings in 'data' are encoded in 'encoding' and change
    them to unicode or utf-8.

    >>> example = [
    ...  ('id', 'body'),
    ...  ('owner', 'jan'),
    ...  ('props', [
    ...    [('id', 'msg_deleted'), ('type', 'string'),
    ...     ('value', 'Datens\xe4tze gel\xf6scht!')],
    ...    [('id', 'content_type'), ('type', 'string'),
    ...     ('value', 'text/html')],
    ...    [('id', 'height'), ('type', 'string'), ('value', 20)],
    ...    [('id', 'expand'), ('type', 'boolean'), ('value', 1)]]),
    ...  ('source', '<p>\\nIm Bereich Limitplanung '
    ...             +'sind die Pl\\xe4ne und Auswertungen '
    ...             +'zusammengefa\\xdft.\\n'),
    ...  ('title', 'Werteplan Monats\xfcbersicht'),
    ...  ('type', 'DTML Method'),
    ... ]
    >>> from pprint import pprint
    >>> pprint(fix_encoding(example, 'iso-8859-1'))
    [('id', 'body'),
     ('owner', 'jan'),
     ('props',
      [[('id', 'msg_deleted'),
        ('type', 'string'),
        ('value', 'Datens\\xc3\\xa4tze gel\\xc3\\xb6scht!')],
       [('id', 'content_type'), ('type', 'string'), ('value', 'text/html')],
       [('id', 'height'), ('type', 'string'), ('value', 20)],
       [('id', 'expand'), ('type', 'boolean'), ('value', 1)]]),
     ('source',
      '<p>\\nIm Bereich Limitplanung sind die Pl\\xc3\\xa4ne und Auswertungen zusammengefa\\xc3\\x9ft.\\n'),
     ('title', 'Werteplan Monats\\xc3\\xbcbersicht'),
     ('type', 'DTML Method')]

    '''
    unpacked = dict(data)
    if 'props' in unpacked:
        unpacked_props = [dict(a) for a in unpacked['props']]
        unpacked['props'] = unpacked_props

    # Skip some types
    skip_types = ['Image', ]
    if unpacked['type'] in skip_types:
        return data

    # Check source
    if 'source' in unpacked and isinstance(unpacked['source'], bytes):
        # Only these types use ustrings, all others stay binary
        ustring_types = [
            # 'Page Template',
            # 'Script (Python)',
        ]
        conversion = unpacked['source'].decode(encoding)
        if unpacked['type'] not in ustring_types:
            conversion = conversion.encode('utf-8')
        unpacked['source'] = conversion

    # Check title
    if 'title' in unpacked and isinstance(unpacked['title'], bytes):
        ustring_types = [
            'Page Template',
        ]
        conversion = unpacked['title'].decode(encoding)
        if unpacked['type'] not in ustring_types:
            conversion = conversion.encode('utf-8')
        unpacked['title'] = conversion

    # Check string properties
    if 'props' in unpacked:
        for prop in unpacked['props']:
            if prop['type'] == 'string':
                prop['value'] = (
                    str(prop['value']).decode(encoding).encode('utf-8')
                )

    if 'props' in unpacked:
        repacked_props = []
        for item in unpacked['props']:
            pack = list(item.items())
            pack.sort()
            repacked_props.append(pack)
        unpacked['props'] = repacked_props
    repacked = list(unpacked.items())
    repacked.sort()
    return repacked


def literal_eval(value):
    '''Literal evaluator (with a bit more power than PT).

    This evaluator is capable of parsing large data sets, and it has
    basic arithmetic operators included.
    '''
    _safe_names = {'None': None, 'True': True, 'False': False}
    if isinstance(value, (type(''), type(b''), type(u''))):
        value = ast.parse(value, mode='eval')

    bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        }

    unary_ops = {
        ast.USub: operator.neg,
    }

    def _convert(node):
        if isinstance(node, ast.Expression):
            return _convert(node.body)
        elif isinstance(node, ast.Str):
            return node.s
        elif isinstance(node, ast.Bytes):
            return node.s
        elif isinstance(node, ast.Num):
            return node.n
        elif isinstance(node, ast.Tuple):
            return tuple(map(_convert, node.elts))
        elif isinstance(node, ast.List):
            return list(map(_convert, node.elts))
        elif isinstance(node, ast.Dict):
            return dict((_convert(k), _convert(v)) for k, v
                        in zip(node.keys, node.values))
        elif isinstance(node, ast.Name):
            if node.id in _safe_names:
                return _safe_names[node.id]
        elif isinstance(node, ast.NameConstant):
            return node.value
        elif isinstance(node, ast.BinOp):
            return bin_ops[type(node.op)](
                _convert(node.left),
                _convert(node.right)
            )
        elif isinstance(node, ast.UnaryOp):
            return unary_ops[type(node.op)](_convert(node.operand))
        else:
            raise Exception('Unsupported type {}'.format(repr(node)))
    return _convert(value)


def cleanup_string(name,
                   valid_chars=string.printable,
                   replacement_char='_',
                   merge_replacements=True,
                   invalid_chars=''):
    '''Sanitize a name. Only valid_chars remain in the string.  Illegal
    characters are replaced with replacement_char. Adjacent
    replacements characters are merged if merge_replacements is True.

    '''
    out = ''
    merge = False
    for i in name:
        # Valid character? Add and continue.
        if (i in valid_chars and i not in invalid_chars):
            out += i
            merge = False
            continue

        # No replacements? No action.
        if not replacement_char:
            continue
        # In merge mode? No action.
        if merge:
            continue

        # Replace.
        out += replacement_char
        if merge_replacements:
            merge = True

    return out


def conserv_split(val, splitby='\n'):
    '''Split by a character, conserving it in the result.'''
    output = [a+splitby for a in val.split(splitby)]
    output[-1] = output[-1][:-len(splitby)]
    if output[-1] == '':
        output.pop()
    return output


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
                 ):
        self.logger = perfact.zodbsync.logger.get_logger('ZODBSync')

        # Load configuration
        if PY2:
            config = imp.load_source('config', conffile)
        else:
            config = importlib.machinery.SourceFileLoader(
                'config', conffile).load_module()

        self.config = config
        self.site = site
        self.base_dir = config.base_dir
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
        self.ignore_objects = [ re.compile('^__'), ]

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
                self.logger.warn('Created user %s with password admin '
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

    def source_ext_from_meta(self, meta):
        '''Guess a good extension from meta data.'''

        obj_id, meta_type, props = None, None, []
        content_type = None

        # Extract meta data from the key-value list passed.
        for key, value in meta:
            if key == 'id':
                obj_id = value
            if key == 'type':
                meta_type = value
            if key == 'props':
                props = value
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

    def fs_write(self, path, data, remove_orphans=True):
        '''Write object data out to a file with the given path.'''

        # Read the basic information
        data_dict = dict(data)
        contents = data_dict.get('contents', [])
        source = data_dict.get('source', None)

        # Only write out sources if unicode or string
        write_source = (type(source) in (type(b''), type(u'')))

        # Build metadata. Remove source from metadata if it is there
        meta = [a for a in data if a[0] != 'source']
        # Only keep contents if we are an ordered folder
        if data_dict['type'] != 'Folder (Ordered)':
            meta = [a for a in meta if a[0] != 'contents']
        fmt = mod_format(meta).encode('utf-8')

        # Make directory for the object if it's not already there
        try:
            os.stat(self.base_dir + '/' + path)
        except OSError:
            self.logger.debug("Will create new directory %s" % path)
            os.makedirs(os.path.join(self.base_dir, path))

        # Metadata
        data_fname = '__meta__'
        # Check if data has changed!
        try:
            old_data = open(self.base_dir + '/' +
                            path + '/' + data_fname, 'rb').read()
        except IOError:
            old_data = None
        if old_data is None or old_data != fmt:
            self.logger.debug("Will write %d bytes of metadata" % len(fmt))
            fh = open(self.base_dir + '/' +
                      path + '/' + data_fname, 'wb')
            fh.write(fmt)
            fh.close()

        # Write source
        if write_source:
            # Check if the source has changed!

            # Write bytes or utf-8 encoded text.
            data = source
            base = '__source__'
            if isinstance(data, unicode):
                data = data.encode('utf-8')
                base = '__source-utf8__'
            ext = self.source_ext_from_meta(meta)
            src_fname = '%s.%s' % (base, ext)
        else:
            src_fname = ''

        # Check if there are stray __source* files and remove them first.
        source_files = [s for s in os.listdir(self.base_dir + '/' + path)
                        if s.startswith('__source') and s != src_fname]
        for source_file in source_files:
            os.remove(os.path.join(self.base_dir, path, source_file))

        if write_source:
            # Check if content has changed!
            try:
                old_data = open(os.path.join(self.base_dir, path, src_fname),
                                'rb').read()
            except IOError:
                old_data = None
            if old_data is None or old_data != data:
                self.logger.debug("Will write %d bytes of source" % len(data))
                fh = open(os.path.join(self.base_dir, path, src_fname), 'wb')
                fh.write(data)
                fh.close()

        if remove_orphans:
            # Check if the contents have changed (are there directories not in
            # "contents"?)
            current_contents = os.listdir(self.base_dir + '/' + path)
            for item in current_contents:
                if self.is_ignored(item):
                    continue

                if item not in contents:
                    self.logger.info("Removing old item %s from filesystem" %
                                     item)
                    shutil.rmtree(os.path.join(self.base_dir, path, item))

        return contents

    def fs_read(self, path):
        '''Read data from local file system.'''
        data_fname = '__meta__'

        filenames = os.listdir(self.base_dir + '/' + path)
        src_fnames = [a for a in filenames if a.startswith('__source')]
        assert len(src_fnames) <= 1, "Multiple source files in " + path
        src_fname = src_fnames and src_fnames[0] or None

        meta_fname = os.path.join(self.base_dir, path, data_fname)
        if os.path.isfile(meta_fname):
            meta_str = open(meta_fname, 'rb').read()
            meta = literal_eval(meta_str)
        else:
            # if a meta file is missing, we assume a dummy folder
            meta = [('title', ''), ('type', 'Folder')]

        if src_fname:
            src = open(self.base_dir + '/' +
                       path + '/' + src_fname, 'rb').read()
            if src_fname.rsplit('.', 1)[0].endswith('-utf8__'):
                src = src.decode('utf-8')
            meta.append(('source', src))
            meta.sort()
        return meta

    def fs_contents(self, path):
        '''Read the current contents from the local file system.'''
        filenames = os.listdir(self.base_dir + '/' + path)
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

    def record(self, path=None, recurse=True):
        '''Record Zope objects from the given path into the local
        filesystem.'''
        obj = self.app
        if path is not None:
            # traverse into the object of interest
            parts = list([_f for _f in path.split('/') if _f])
            for part in parts:
                obj = getattr(obj, part)
        self.record_obj(obj, recurse=recurse)

    def record_obj(self, obj, recurse=True):
        '''Record a Zope object into the local filesystem'''

        data = mod_read(obj, default_owner=self.default_owner)
        path = self.site + ('/'.join(obj.getPhysicalPath()))
        contents = self.fs_write(path, data, remove_orphans=recurse)

        # Update statistics
        self.num_obj_current += 1
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
            # Check if one of the ignore patterns matches
            if self.is_ignored(item):
                continue

            # Recurse
            if recurse:
                new_obj = getattr(obj, item)
                self.record_obj(obj=new_obj)

    def playback(self, path=None, recurse=True, override=False,
                 skip_errors=False, encoding=None):
        '''Play back (write) objects from the local filesystem into Zope.'''
        self.num_obj_current += 1
        if not recurse:
            # be more verbose because every path is explicitly requested
            self.logger.info('Uploading %s' % path)
        path = path or ''
        parts = [part for part in path.split('/') if part]
        if 'get' in parts:
            self.logger.warn('Object "get" cannot be uploaded at path %s' %
                             path)
            return

        # Step through the path components as well as the object tree.
        folder = self.base_dir + '/' + self.site
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

        fs_path = self.site + '/' + path
        fs_data = dict(self.fs_read(fs_path))
        if 'unsupported' in fs_data:
            self.logger.warn('Skipping unsupported object ' + path)
            return

        if encoding is not None:
            # Translate file system data
            fs_data = dict(fix_encoding(fs_data, encoding))

        srv_data = (
            dict(mod_read(obj, default_owner=self.manager_user))
            if obj_exists else None
        )
        # Only keep contents if we are an ordered folder
        if (srv_data and srv_data['type'] != 'Folder (Ordered)'
                and 'contents' in srv_data):
            del srv_data['contents']

        if fs_data != srv_data:
            self.logger.debug("Uploading: %s:%s" % (path, fs_data['type']))
            try:
                obj = mod_write(
                    fs_data,
                    parent=parent_obj,
                    obj_id=part,
                    override=override,
                    root=(obj if parent_obj is None else None),
                    default_owner=self.default_owner
                )
            except:
                # If we do not want to get errors from missing
                # ExternalMethods, this can be used to skip them
                severity = 'Skipping' if skip_errors else 'ERROR'
                msg = '%s %s:%s' % (severity, path, fs_data['type'])
                if skip_errors:
                    self.logger.warn(msg)
                    return
                else:
                    self.logger.error(msg)
                    raise

        if not recurse:
            return

        contents = self.fs_contents(fs_path)

        # Update statistics
        self.num_obj_total += len(contents)
        now = time.time()
        if now - self.num_obj_last_report > 2:
            self.logger.info(
                '%d obj checked of at least %d, current path %s'
                % (self.num_obj_current, self.num_obj_total, path)
            )
            self.num_obj_last_report = now

        if obj and hasattr(obj, 'objectItems'):
            # Read contents from obj
            srv_contents = [a[0] for a in obj.objectItems()]
        else:
            srv_contents = []
        # Find IDs in Data.fs object not present in file system
        del_ids = [a for a in srv_contents if a not in contents]
        if del_ids:
            self.logger.warn('Deleting objects ' + repr(del_ids))
            obj.manage_delObjects(ids=del_ids)

        for item in contents:
            if self.is_ignored(item):
                continue
            self.playback(path=os.path.join(path, item), override=override,
                    encoding=encoding, skip_errors=skip_errors)

        # sort children for ordered folders
        if fs_data['type'] == 'Folder (Ordered)':
            contents = fs_data.get('contents', [])
            srv_contents = [a[0] for a in obj.objectItems()]

            # only use contents that are present in the object
            contents = [a for a in contents if a in srv_contents]
            obj.moveObjectsByDelta(contents, -len(contents))

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
        path = '__last_txn__'
        fh = open(self.base_dir + '/' + path, 'wb')
        fh.write(txnid)
        fh.close()

    def txn_read(self):
        '''Read the newest transaction ID'''
        path = '__last_txn__'
        try:
            txn = open(self.base_dir + '/' + path, 'rb').read()
        except IOError:
            txn = None
        return txn
