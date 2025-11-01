#!/usr/bin/env python

import base64
import signal
import time
import threading
import sys
import pickle
import subprocess

# For reading the Data.FS in order to obtain affected object IDs from
# transaction IDs
import ZODB.FileStorage

from ..subcommand import SubCommand
from ..helpers import increment_txnid
from ..zodbsync import mod_read


class TreeOutdatedException(Exception):
    """Exception which is raised if the internal tree structure
    is not matching the actual Filesystem anymore"""
    pass


class Watch(SubCommand):
    """Periodically check for changes and record them"""
    # Connects to ZEO, builds a mirror of the tree structure of the objects,
    # periodically checks for new transactions, looks directly into the Data.FS
    # to get the object IDs affected by those transactions, and updates its
    # tree structure as well as the file system tree structure accordingly.

    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--init',
            action='store_true',
            default=False,
            help='Internal mode for initialization subprocess',
        )

    def __init__(self, **kw):
        super(Watch, self).__init__(**kw)
        self.base_dir = self.sync.app_dir
        self.app = self.sync.app
        # an event that is fired if we are to be terminated
        self.exit = threading.Event()

        try:
            self.datafs_path = self.config["datafs_path"]
        except AttributeError:
            self.logger.exception("watch requires datafs_path in config")
            raise

        # mapping from object id to dict describing tree structure
        self.object_tree = {}

        # Mapping of additional object ids to OIDs of recorded objects. This is
        # currently only used for `User Folder`s which contain a
        # `PersistentMapping` and `User`s, which have their own OID and are not
        # children in the sense that they can be obtained using objectIds(). If
        # one of the additional OIDs is found to have been changed, the
        # original OID is assumed to have been changed instead.
        # If it turns out that it is possible to move a `User` from one `User
        # Folder` to another, they should instead be represented as separate
        # objects, but at least the management interface does not provide a
        # method for this.
        self.additional_oids = {}

    def _set_last_visible_txn(self):
        ''' Set self.last_visible_txn to a transaction ID such that every
        effect up to this ID is visible in the current transaction and every
        effect for transaction IDs above this are not yet visible.
        '''
        self.last_visible_txn = self.app._p_jar._db.lastTransaction()

    def _store_last_visible_txn(self):
        '''
        Store last visible transaction ID to disk if it changed.
        '''
        if self.last_visible_txn != self.txnid_on_disk:
            self.txnid_on_disk = self.last_visible_txn
            self.sync.txn_write(base64.b64encode(self.last_visible_txn))

    def _init_tree(self, obj, parent_oid=None, path='/'):
        ''' Insert obj and everything below into self.object_tree. '''
        if not hasattr(obj, '_p_oid'):
            # objects that have no oid are ignored
            return None
        # In some Python/Zope versions, _p_oid is a zodbpickle.binary, which is
        # not pickleable. We always convert it into bytes.
        oid = bytes(obj._p_oid)

        children = {}  # map oid -> id

        now = time.time()
        if self.last_report is None:
            self.last_report = now
        if now - self.last_report > 2:
            self.logger.info("Building tree: " + path)
            self.last_report = now

        self.object_tree[oid] = {
            'parent': parent_oid,
            'children': children,
            'path': path,
        }

        # If it turns out there are other objects needing such a hack, this
        # should probably be moved to object_types
        if obj.meta_type == 'User Folder':
            self.additional_oids[bytes(obj.data._p_oid)] = oid
            for user in obj.getUsers():
                self.additional_oids[bytes(user._p_oid)] = oid

        for child_id, child_obj in sorted(obj.objectItems()):
            child_oid = self._init_tree(
                obj=child_obj,
                parent_oid=oid,
                path=path+child_id+'/'
            )
            if child_oid:
                children[child_oid] = child_id
        return oid

    def _read_changed_oids(self, txn_start, txn_stop):
        """
        Return a set of object IDS that are affected by the transactions with
        IDs between start and stop (incl.)
        """
        # FileIterator opens the Data.FS read-only and provides the following
        # fields and methods:
        # * _file: The file object for the opened Data.FS
        # * _read_data_header(pos): reads a data header at some position in the
        #   file
        # * _ltid: the transaction id that was last read.
        # It is also possible to iterate over FileIterator, which yields
        # transactions in the form of a TransactionRecord
        self.changed_oids = set()
        if txn_start > txn_stop:
            return
        storage = ZODB.FileStorage.FileIterator(
            self.datafs_path,
            start=txn_start,
            stop=txn_stop,
        )
        for txn in storage:
            # Each TransactionRecord has the following fields:
            # * _pos: Position of the first data header
            # * _tend: End of the last data block
            # * _file: A reference to the file
            pos = txn._pos
            while pos < txn._tend:
                dhead = storage._read_data_header(pos)
                # each data header has the fields and methods
                # * _recordlen(): gives the total length. Adding recordlen()
                #   advances to the next data header
                # * oid: the object ID
                # * plen: the size of the pickle data, which comes after the
                #   header
                dlen = dhead.recordlen()
                oid = self.additional_oids.get(bytes(dhead.oid),
                                               bytes(dhead.oid))
                self.changed_oids.add(oid)
                pos = pos + dlen

    def _remove_subtree(self, oid):
        """
        Remove a subtree from self.object_tree
        """
        todo = [oid]
        while todo:
            oid = todo.pop()
            todo.extend(self.object_tree[oid]['children'])
            del self.object_tree[oid]

    def _record_object(self, oid):
        '''
        Store data of an object at the path stored in our object tree.
        '''
        path = self.object_tree[oid]['path']
        self.logger.info('Recording %s' % path)
        self.logger.debug('OID: ' + repr(oid))

        obj = self.app._p_jar[oid]
        data = mod_read(
            obj=obj,
            default_owner=self.sync.default_owner
        )

        pathinfo = self.sync.fs_write(path=path, data=data)
        path_layer = pathinfo['layers'][pathinfo['layeridx']]['ident']
        current_layer = getattr(obj, 'zodbsync_layer', None)
        if current_layer != path_layer:
            with self.sync.tm:
                obj.zodbsync_layer = path_layer

    def _update_objects(self):
        '''
        Run through the changed oids and update the tree and the file system
        accordingly.
        '''

        if not len(self.changed_oids):
            return
        self.logger.info('Found %s changed objects' % len(self.changed_oids))
        self.logger.debug('OIDs: ' + str(sorted(self.changed_oids)))

        while len(self.changed_oids):
            # not all oids are part of our object tree yet, so we have to
            # iteratively update some at a time
            next_oids = self.changed_oids.intersection(self.object_tree.keys())
            if not len(next_oids):
                # The remaining oids are not reachable by any of the currently
                # existing nodes. This can happen during initialization, since
                # the tree structure is created as visible at the end of the
                # transaction chain and then affected objects are collected for
                # earlier transactions, but they might no longer exist
                break
            for oid in next_oids:
                if oid not in self.object_tree:
                    # Might have vanished from the tree in an earlier iteration
                    # because the parent was moved or renamed.
                    continue
                self._record_object(oid=oid)
                self._update_children(oid=oid)
                self.changed_oids.remove(oid)

        self.sync.fs_prune_empty_dirs()

    def _update_children(self, oid):
        '''
        Check the current children of an object and compare with the stored
        children. Remove any superfluous children (oid not found or wrong id)
        and record any new children recursively.
        '''
        obj = self.app._p_jar[oid]
        node = self.object_tree[oid]

        newchildren = {}
        for child_id, child_obj in obj.objectItems():
            if not hasattr(child_obj, '_p_oid'):
                continue
            newchildren[bytes(child_obj._p_oid)] = child_id

        # Go through old children and check if some are to be deleted
        for child_oid, child_id in list(node['children'].items()):
            if newchildren.get(child_oid) == child_id:
                continue
            self._remove_subtree(child_oid)
            del node['children'][child_oid]

        pathinfo = self.sync.fs_pathinfo(node['path'])
        self.sync.fs_prune(pathinfo, newchildren.values())

        # Add new children to changed_oids so they will also be recorded
        for child_oid, child_id in list(newchildren.items()):
            if child_oid in node['children']:
                continue
            newpath = node['path']+child_id+'/'

            if child_oid in self.object_tree:
                # The parent changed. Remove from there. Since the old parent
                # will also be changed, the call to fs_prune there will take
                # care of removing everything on the FS.
                old_parent = self.object_tree[child_oid]['parent']
                del self.object_tree[old_parent]['children'][child_oid]
                self._remove_subtree(child_oid)

            self.changed_oids.add(child_oid)
            self.object_tree[child_oid] = {
                'parent': oid,
                'children': {},
                'path': newpath,
            }
            node['children'][child_oid] = child_id

    def quit(self, signo, _frame):
        """
        Signal handler
        """
        self.unregister_signals()
        self.exit.set()

    def register_signals(self):
        for sig in ('TERM', 'HUP', 'INT'):
            signal.signal(getattr(signal, 'SIG'+sig), self.quit)

    def unregister_signals(self):
        for sig in ('TERM', 'HUP', 'INT'):
            signal.signal(getattr(signal, 'SIG'+sig), signal.SIG_DFL)

    def setup(self):
        """
        Initially create tree and record anything that happened since the last
        running.
        """

        # During initialization, we report progress every 2 seconds.
        self.last_report = None

        # During normal operation, we always assume that the hard disk tree
        # structure is mirrored in object_tree, which mirrors the ZODB after
        # transaction A. When reading data in our Zope instance, we see the
        # ZODB after some later transaction B. We obtain the list of changed
        # object ids between A and B. Then we look up all objects that we know
        # of which were changed, record their meta data and move children
        # around, until our tree as well as the file system mirrors the state
        # at B (and our list of changed objects is empty).
        #
        # However, at startup the situation is different. Our object tree is
        # the same that we see through our Zope instance, which is the state
        # after B. The file system, on the other hand, mirrors the state after
        # A. Since we do not want to read the whole tree structure after A from
        # the file system (which would also require to store the OIDs), we do
        # not know which move operations would take us from A to B. Instead, we
        # collect a list of all changed paths and record them recursively.

        self.acquire_lock(timeout=300)
        self._set_last_visible_txn()
        self.sync.tm.begin()
        self._init_tree(self.app)

        # the transaction ID stored on disk is the last transaction whose
        # changes have already been recorded to disk. We increase it by one to
        # obtain all changes after that one
        self.txnid_on_disk = self.sync.txn_read()

        if self.txnid_on_disk is None:
            # no txnid found, record everything
            paths = ['/']
        else:
            self.txnid_on_disk = base64.b64decode(self.txnid_on_disk)
            txn_start = increment_txnid(self.txnid_on_disk)

            # obtain all object ids affected by transactions between (the one
            # in last_txn + 1) and (the currently visible one) (incl.)
            self._read_changed_oids(
                txn_start=txn_start,
                txn_stop=self.last_visible_txn
            )
            paths = []
            while len(self.changed_oids):
                next_oids = self.changed_oids.intersection(
                    self.object_tree.keys()
                )
                if not len(next_oids):
                    # The remaining oids are not reachable by any of the
                    # currently existing nodes. This can happen during
                    # initialization since the tree structure is created as
                    # visible at the end of the transaction chain and then
                    # affected objects are collected for earlier transactions,
                    # but they might no longer exist
                    break
                paths.extend(
                    [self.object_tree[oid]['path'] for oid in next_oids]
                )
                self.changed_oids.difference_update(next_oids)

        self.sync.record(paths)

        self.sync.tm.abort()

        # store an updated txnid on disk
        self._store_last_visible_txn()

        self.release_lock()

        self.logger.info("Setup complete")

    def spawned_setup(self):
        """
        Run setup in a separate process to reduce memory footprint of main
        process. During setup, the complete object tree is read into memory,
        which is not really released. Afterwards, the main process runs for a
        long time and uses much less memory. Therefore, we use a separate
        process.
        """
        cmd = [sys.executable, sys.argv[0], '--config', self.args.config,
               'watch', '--init']
        data = pickle.loads(subprocess.check_output(cmd))
        self.object_tree = data['tree']
        self.additional_oids = data['add_oids']
        self.last_visible_txn = self.txnid_on_disk = data['txn']

    def dump_setup_data(self, stream=sys.stdout):
        """
        Print pickled setup data for usage in main process.
        """
        data = {
            'tree': self.object_tree,
            'add_oids': self.additional_oids,
            'txn': self.last_visible_txn,
        }
        # write binary to stdout - in Py3, this requires using
        # sys.stdout.buffer, in Py2 sys.stdout itself is used.
        pickle.dump(data, file=getattr(stream, 'buffer', stream))

    def step(self):
        """Read new transactions, update the object tree and record all
        changes."""
        self.unregister_signals()
        self.acquire_lock(timeout=300)
        try:
            self.register_signals()

            start_txnid = increment_txnid(self.last_visible_txn)
            self._set_last_visible_txn()
            self._read_changed_oids(
                txn_start=start_txnid,
                txn_stop=self.last_visible_txn,
            )
            # make sure we see a consistent snapshot, even though we later
            # abort this transaction since we do not write anything
            self.sync.tm.begin()
            self._update_objects()
            self.sync.tm.abort()

            self._store_last_visible_txn()
        except TreeOutdatedException:
            self.logger.info(
                'Exiting due to inconsistencies in filesystem'
            )
            self.exit.set()
        finally:
            self.release_lock()

    def run(self, interval=10):
        """ Setup and run in a loop. """
        if self.args.init:
            self.setup()
            self.dump_setup_data()
            return
        else:
            self.spawned_setup()
        while not self.exit.is_set():
            self.step()
            # a wait that is interrupted immediately if exit.set() is called
            self.exit.wait(interval)

        self.logger.info('Exited due to signal')
