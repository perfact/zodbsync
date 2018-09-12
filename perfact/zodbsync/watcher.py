#!/usr/bin/env python

import perfact.zodbsync.zodbsync
import base64
import signal
import time
import threading

# For reading the Data.FS in order to obtain affected object IDs from
# transaction IDs
import ZODB.FileStorage

# for making an annotation to the transaction
import transaction
# for "logging in"
import AccessControl.SecurityManagement 

# Logging
from perfact.zodbsync.logger import get_logger
logger = get_logger('ZODBSyncWatcher')

# Helpers for handling transaction IDs (which are byte strings of length 8)
def _decrement_txnid(s):
    ''' subtract 1 from s, but for s being a string of bytes'''
    arr = [c for c in s]
    pos = len(arr)-1
    while pos >= 0:
        c = ord(arr[pos])
        if c == 0:
            arr[pos] = chr(255)
            pos -= 1
        else:
            arr[pos] = chr(c-1)
            break
    return ''.join(arr)

def _increment_txnid(s):
    ''' add 1 to s, but for s being a string of bytes'''
    arr = [c for c in s]
    pos = len(arr)-1
    while pos >= 0:
        c = ord(arr[pos])
        if c == 255:
            arr[pos] = chr(0)
            pos -= 1
        else:
            arr[pos] = chr(c+1)
            break
    return ''.join(arr)

class ZODBSyncWatcher:
    """
    """
    def __init__(self, sync, config):
        self.sync = sync
        self.app = sync.app
        if not hasattr(config, 'datafs_path'): 
            err = "--watch requires datafs_path in config"
            logger.error(err)
            raise AssertionError(err)
        self.datafs_path = config.datafs_path

        # read the complete tree structure of the ZODB, allowing obtaining
        # paths by object ids and therefore incrementally updating the object
        # database.

        # Log in as a manager
        uf = self.app.acl_users
        user = uf.getUserById(self.sync.manager_user)
        if not hasattr(user, 'aq_base'):
            user = user.__of__(uf)
        AccessControl.SecurityManagement.newSecurityManager(None, user)

        # mapping from object id to dict describing tree structure
        self.object_tree = {} 
        self.last_report = None

        # Store the transaction ID that is currently visible and build the
        # corresponding (currently visible) tree
        transaction.begin()
        self._set_last_visible_txn()
        self._build_tree(self.app)
        transaction.abort()

        # the transaction ID stored on disk is the last transaction whose
        # changes have already been recorded to disk. We increase it by one to
        # obtain all changes after that one
        self.txnid_on_disk = self.sync.txn_read()
        if self.txnid_on_disk is None:
            # no txnid found, record '/'
            oids = [self.app._p_oid]
        else:
            self.txnid_on_disk = base64.b64decode(self.txnid_on_disk)
            txn_start = _increment_txnid(self.txnid_on_disk)

            # obtain all object ids affected by transactions between (the one in
            # last_txn + 1) and (the currently visible one) (incl.)
            oids = self._get_object_ids(
                    txn_start = txn_start, 
                    txn_stop = self.last_visible_txn)

        # record all affected objects
        self._record_objects(oids = oids)

        # store an updated txnid on disk
        if self.txnid_on_disk != self.last_visible_txn:
            self.txnid_on_disk = self.last_visible_txn
            self.sync.txn_write(base64.b64encode(self.last_visible_txn))
        logger.info("Setup complete")

    def update(self):
        ''' check for new transactions and record affected objects. '''

        # make sure we see a consistent snapshot, even though we later abort
        # this transaction since we do not write anything
        transaction.begin()
        start_txnid = _increment_txnid(self.last_visible_txn)
        self._set_last_visible_txn()
        oids = self._get_object_ids(
                txn_start = start_txnid,
                txn_stop = self.last_visible_txn,
                )
        # update tree
        remaining_oids = oids
        removed_oids = []
        orphan_candidates = []
        while len(remaining_oids):
            # not all oids are part of our object tree yet, so we have to
            # iteratively update some at a time
            next_oids = [oid for oid in remaining_oids 
                    if oid in self.object_tree]
            if not len(next_oids):
                # The remaining oids are not reachable by any of the currently
                # existing nodes. This can happen during initialization, since
                # the tree structure is created as visible at the end of the
                # transaction chain and then affected objects are collected for
                # earlier transactions, but they might no longer exist
                break

            for oid in next_oids:
                obj = self.object_tree[oid]
                # update ID in case it changed
                obj['id'] = '' if obj['parent'] is None else obj['obj'].getId()
                newchildren = []
                for child in obj['obj'].objectValues():
                    if not hasattr(child, '_p_oid'):
                        continue
                    newchildren.append(child._p_oid)
                    if child._p_oid in self.object_tree:
                        self.object_tree[child._p_oid]['parent'] = oid
                        newchildren.append(child._p_oid)
                        if child._p_oid in orphan_candidates:
                            # not orphaned after all, just moved
                            orphan_candidates.remove(child._p_oid)
                    else:
                        self.last_report = None
                        newchildren.append(
                                self._build_tree(child, parent=oid))
                for oldchild in obj['children']:
                    if oldchild in newchildren:
                        continue
                    # the child might be orphaned or moved somewhere else
                    child = self.object_tree[oldchild]
                    if child['parent'] != oid:
                        # new parent was already handled
                        continue
                    # the new parent might turn up later, so don't orphan
                    # immediately
                    orphan_candidates.append(oldchild)
                    child['parent'] = None
                obj['children'] = newchildren

            remaining_oids = [oid for oid in remaining_oids 
                    if oid not in next_oids]
        # the remaining operations happen on our tree structure, so we do no
        # longer need a consistent snapshot of the ZODB
        transaction.abort()

        # all abandoned children that have not been picked up by other parents
        for orphan in orphan_candidates:
            self._orphan_node(orphan)

        self._record_objects(oids)
        if self.last_visible_txn != self.txnid_on_disk:
            self.txnid_on_disk = self.last_visible_txn
            self.sync.txn_write(base64.b64encode(self.last_visible_txn))

    def run(self, interval=10):
        '''Periodically read new transactions, update the object tree and
        record all changes. Handles SIGTERM and SIGINT so any running recording
        is finished before terminating.'''

        # an event that is fired if we are to be terminated
        exit = threading.Event()

        # event handler for signals
        def quit(signo, _frame):
            logger.info('Caught signal, exiting...')
            exit.set()
        for sig in ('TERM', 'HUP', 'INT'):
            signal.signal(getattr(signal, 'SIG'+sig), quit);

        while not exit.is_set():
            self.update()
            # a wait that is interrupted immediately if exit.set() is called
            exit.wait(interval) 
        logger.info('')


    def _build_tree(self, obj, parent=None, path=''):
        ''' insert obj and everything below into self.object_tree. '''
        children = []
        try:
            oid = obj._p_oid
        except AttributeError:
            return None
        objid = '' if parent is None else obj.getId()
        self.object_tree[oid] = {
                'oid': oid,
                'obj': obj, # todo: omit this
                'id': objid,
                'parent': parent,
                'children': children,
                }
        if not path.endswith('/'):
            path = path + '/'
        path = path + objid
        now = time.time()
        if self.last_report is None:
            self.last_report = now
        if now - self.last_report > 2:
            logger.info("Building tree: " + path)
            self.last_report = now
        for child in sorted(obj.objectValues(), key=lambda x: x.getId()):
            child_oid = self._build_tree(child, 
                    parent=oid, path=path)
            if child_oid:
                children.append(child_oid)
        return oid

    def _set_last_visible_txn(self):
        ''' Set self.last_visible_txn to a transaction ID such that every
        effect up to this ID is visible in the current transaction and every
        effect for transaction IDs above this are not yet visible.
        '''
        # by default, take the last transaction in the undo record
        records = self.app._p_jar.db().undoInfo(0,1)
        if not len(records): 
            # after packing, no undo records exist. Return zero
            self.last_visible_txn = chr(0)*8
            return
        self.last_visible_txn = base64.b64decode(records[0]['id'])
        # check if there are transactions we do not see yet
        if self.app._p_jar._txn_time:
            # if this is set, it is the first transaction that we can not yet
            # see, so we subtract one
            self.last_visible_txn = _decrement_txnid(self.app._p_jar._txn_time)

    def _get_object_ids(self, txn_start, txn_stop):
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
        storage = ZODB.FileStorage.FileIterator(self.datafs_path, 
                start = txn_start, stop = txn_stop)
        oids = set()
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
                # * plen: the size of the pickle data, which comes after the header
                dlen = dhead.recordlen()
                oids.add(dhead.oid)
                pos = pos + dlen
        return oids
    
    def _record_objects(self, oids):
        ''' record objects with given object ids '''
        paths = []
        for oid in oids:
            if oid not in self.object_tree:
                continue
            obj = self.object_tree[oid]
            parts = [obj['id'],]
            while obj['parent'] is not None:
                obj = self.object_tree[obj['parent']]
                parts.insert(0,obj['id'] if obj['parent'] is not None else '')
            paths.append('/'.join(parts) + '/')
        # remove subpaths, i.e. if /a/b/ as well as /a/ are in paths, only keep
        # /a/, since by recursion /a/b/ will also be updated
        paths.sort()
        i = 0
        while i < len(paths):
            while (i+1 < len(paths) and
                    paths[i+1].startswith(paths[i])):
                del paths[i+1]
            i += 1
        for path in paths:
            logger.info("Recording %s", path)
            self.sync.record(path)

    def _orphan_node(self,oid):
        obj = self.object_tree[oid]
        del self.object_tree[oid]
        for child in obj['children']:
            if (child in self.object_tree
                    and self.object_tree[child]['parent'] == oid):
                self._orphan_node(child)

