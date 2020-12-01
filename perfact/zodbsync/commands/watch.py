#!/usr/bin/env python

import base64
import binascii
import signal
import time
import threading
import os
import shutil

# For reading the Data.FS in order to obtain affected object IDs from
# transaction IDs
import ZODB.FileStorage

# for making an annotation to the transaction
import transaction
# for "logging in"
import AccessControl.SecurityManagement

from ..subcommand import SubCommand
from ..helpers import remove_redundant_paths
from ..zodbsync import mod_read


# Helpers for handling transaction IDs (which are byte strings of length 8)
def _decrement_txnid(s):
    ''' subtract 1 from s, but for s being a string of bytes'''
    arr = bytearray(s)
    pos = len(arr)-1
    while pos >= 0:
        if arr[pos] == 0:
            arr[pos] = 255
            pos -= 1
        else:
            arr[pos] -= 1
            break
    return bytes(arr)


def _increment_txnid(s):
    ''' add 1 to s, but for s being a string of bytes'''
    arr = bytearray(s)
    pos = len(arr)-1
    while pos >= 0:
        if arr[pos] == 255:
            arr[pos] = 0
            pos -= 1
        else:
            arr[pos] += 1
            break
    return bytes(arr)


class Watch(SubCommand):
    """
    Sub-command that connects to ZEO, builds a mirror of the tree structure of
    the objects, periodically checks for new transactions, looks directly into
    the Data.FS to get the object IDs affected by those transactions, and
    updates its tree structure as well as the file system tree structure
    accordingly.
    """

    def _set_last_visible_txn(self):
        ''' Set self.last_visible_txn to a transaction ID such that every
        effect up to this ID is visible in the current transaction and every
        effect for transaction IDs above this are not yet visible.
        '''
        # by default, take the last transaction in the undo record
        records = self.app._p_jar.db().undoInfo(0, 1)
        if not len(records):
            # after packing, no undo records exist. Return zero
            self.last_visible_txn = b'\x00'*8
            return
        self.last_visible_txn = base64.b64decode(records[0]['id'])
        # check if there are transactions we do not see yet
        if getattr(self.app._p_jar, '_txn_time', None):
            # if this is set, it is the first transaction that we can not yet
            # see, so we subtract one
            self.last_visible_txn = _decrement_txnid(
                self.app._p_jar._txn_time
            )

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
        oid = obj._p_oid

        children = {}  # map oid -> id

        now = time.time()
        if self.last_report is None:
            self.last_report = now
        if now - self.last_report > 2:
            self.logger.info("Building tree: " + path)
            self.last_report = now

        self.object_tree[oid] = {
            'oid': oid,
            'parent': parent_oid,
            'children': children,
            'path': path,
        }

        # If it turns out there are other objects needing such a hack, this
        # should probably be moved to object_types
        if obj.meta_type == 'User Folder':
            self.additional_oids[obj.data._p_oid] = oid
            for user in obj.getUsers():
                self.additional_oids[user._p_oid] = oid

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
        storage = ZODB.FileStorage.FileIterator(
            self.datafs_path,
            start=txn_start,
            stop=txn_stop,
        )
        self.changed_oids = set()
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
                oid = self.additional_oids.get(dhead.oid, dhead.oid)
                self.changed_oids.add(oid)
                pos = pos + dlen

    def _update_path(self, oid, path):
        '''
        If an element has been moved, this is called to update the path for the
        subtree
        '''
        node = self.object_tree[oid]
        node['path'] = path
        for child_oid, child_id in node['children'].items():
            self._update_path(oid=child_oid, path=path+child_id+'/')

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
        self.sync.fs_write(path=path, data=data)

    def _update_objects(self):
        '''
        Run through the changed oids and update the tree and the file system
        accordingly.
        '''

        # Any child that is no longer wanted by a parent (i.e., no longer found
        # in its objectItems()), is stored in adoption_list. If no new
        # parent is found by the end of the routine, they are removed :-(.
        # If someone adopts a child (has a new child that was already part of
        # our object tree), it is immediately removed from its former parent
        # and from the adoption list if present.

        if not len(self.changed_oids):
            return
        self.logger.info('Found %s changed objects' % len(self.changed_oids))
        self.logger.debug('OIDs: ' + str(sorted(self.changed_oids)))

        self.adoption_list = set()
        shutil.rmtree(self.base_dir+'/../__orphans__/', ignore_errors=True)

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
                self._record_object(oid=oid)
                self._update_children(oid=oid)

            self.changed_oids.difference_update(next_oids)

        remove_paths = []
        while len(self.adoption_list):
            oid = self.adoption_list.pop()
            node = self.object_tree[oid]
            del self.object_tree[oid]

            # recursively remove children from tree
            self.adoption_list.update(node['children'])
            parent_oid = node['parent']
            if (parent_oid in self.object_tree
                    and oid in self.object_tree[parent_oid]['children']):
                del self.object_tree[parent_oid]['children'][oid]
            remove_paths.append(node['path'])

        remove_redundant_paths(remove_paths)
        for path in remove_paths:
            self.logger.info('Removing %s' % path)
            shutil.rmtree(self.base_dir+path)

    def _update_children(self, oid):
        '''
        Check the current children of an object and compare with the stored
        children. Rename children that changed their name, adopt new children
        that previously had different parents, create new children, and set
        obsolete children up for adoption.
        '''
        obj = self.app._p_jar[oid]
        node = self.object_tree[oid]

        newchildren = {}
        for child_id, child_obj in obj.objectItems():
            if not hasattr(child_obj, '_p_oid'):
                continue
            newchildren[child_obj._p_oid] = child_id

        # go through old children and check if they are still there
        for child_oid, child_id in list(node['children'].items()):
            if (child_oid not in newchildren
                    or child_id != newchildren[child_oid]):
                # Put up for adoption. The new parent might show up later or it
                # might be the same but the child was renamed.  However, we
                # need to move the folder away immediately in case another
                # object takes its place
                self.adoption_list.add(child_oid)
                del node['children'][child_oid]
                self.object_tree[child_oid]['parent'] = None
                oldpath = self.object_tree[child_oid]['path']
                newpath = ('/../__orphans__/' +
                           binascii.hexlify(child_oid).decode('ascii')
                           )
                self.logger.info(
                    'Moving %s => %s' % (
                        oldpath,
                        newpath
                    )
                )
                os.makedirs(self.base_dir+newpath)
                os.rename(self.base_dir+oldpath, self.base_dir+newpath)
                self._update_path(child_oid, newpath)

        # go through new children and check if they have old parents
        for child_oid, child_id in list(newchildren.items()):
            if child_oid in node['children']:
                continue
            newpath = node['path']+child_id+'/'

            if child_oid in self.object_tree:
                # the parent changed
                child = self.object_tree[child_oid]
                self.logger.info(
                    'Moving %s => %s' % (
                        child['path'],
                        newpath,
                    )
                )
                os.rename(
                    self.base_dir+child['path'],
                    self.base_dir+newpath
                )
                if (child['parent'] is not None
                        and child['parent'] in self.object_tree):
                    children = self.object_tree[child['parent']]['children']
                    del children[child_oid]
                child['parent'] = oid
                self._update_path(child_oid, node['path']+child_id+'/')
                if child_oid in self.adoption_list:
                    self.adoption_list.remove(child_oid)
            else:
                # A new child not yet known in our tree. Usually, it will
                # already be in changed_oids and the following is a no-op -
                # except if an object hierarchy was resurrected by an Undo.
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
        self.logger.info('Caught signal, exiting...')
        self.unregister_signals()
        exit.set()

    def register_signals(self):
        for sig in ('TERM', 'HUP', 'INT'):
            signal.signal(getattr(signal, 'SIG'+sig), self.quit)

    def unregister_signals(self):
        for sig in ('TERM', 'HUP', 'INT'):
            signal.signal(getattr(signal, 'SIG'+sig), signal.SIG_DFL)

    def run(self, interval=10):
        '''Periodically read new transactions, update the object tree and
        record all changes. Handles SIGTERM and SIGINT so any running recording
        is finished before terminating.'''

        self.base_dir = os.path.join(self.sync.base_dir, self.sync.site)
        self.app = self.sync.app

        try:
            self.datafs_path = self.config["datafs_path"]
        except AttributeError:
            self.logger.exception("watch requires datafs_path in config")
            raise

        # Log in as a manager
        uf = self.app.acl_users
        user = uf.getUserById(self.sync.manager_user)
        if not hasattr(user, 'aq_base'):
            user = user.__of__(uf)
        AccessControl.SecurityManagement.newSecurityManager(None, user)

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

        self.sync.acquire_lock(timeout=300)
        transaction.begin()
        self._set_last_visible_txn()
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
            txn_start = _increment_txnid(self.txnid_on_disk)

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

        remove_redundant_paths(paths)
        for path in paths:
            self.logger.info('Recording %s' % path)
            self.sync.record(path)

        transaction.abort()

        # store an updated txnid on disk
        self._store_last_visible_txn()

        self.sync.release_lock()

        self.logger.info("Setup complete")

        # an event that is fired if we are to be terminated
        exit = threading.Event()

        while not exit.is_set():
            self.unregister_signals()
            self.sync.acquire_lock(timeout=300)
            self.register_signals()

            # make sure we see a consistent snapshot, even though we later
            # abort this transaction since we do not write anything
            transaction.begin()
            start_txnid = _increment_txnid(self.last_visible_txn)
            self._set_last_visible_txn()
            self._read_changed_oids(
                txn_start=start_txnid,
                txn_stop=self.last_visible_txn,
            )
            self._update_objects()
            transaction.abort()

            self._store_last_visible_txn()
            self.sync.release_lock()

            # a wait that is interrupted immediately if exit.set() is called
            exit.wait(interval)
