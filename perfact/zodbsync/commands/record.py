#!/usr/bin/env python

import sys

try:
    # for git snapshot
    import perfact.pfcodechg
except:
    pass

from ..subcommand import SubCommand


class Record(SubCommand):
    ''' Sub-command to record objects from the Data.FS to the file system.
    '''
    def add_args(self, parser):
        parser.add_argument(
            '--lasttxn', action='store_true', default=False,
            help='Add paths mentioned in transactions since the last used',
        )
        if 'perfact.pfcodechg' in sys.modules:
            parser.add_argument(
                '--commit', action='store_true', default=False,
                help='Commit changes and send summary mail if there are'
                ' changes.',
            )
        parser.add_argument(
            '--no-recurse', action='store_true', default=False,
            help='Record only specified paths without recursing',
        )
        parser.add_argument(
            'path', type=str, nargs='*',
            help='Sub-Path in Data.fs to be recorded',
        )

    def run(self, args, sync):
        sync.acquire_lock()
        paths = args.path
        recurse = not args.no_recurse

        if args.lasttxn:
            # We mean to read from the newest entry
            lasttxn = sync.txn_read() or None

            res = sync.recent_changes(
                since_secs=None,
                txnid=lasttxn,
                limit=51,
            )
            newest_txnid = res['newest_txnid']
            if (res['search_limit_reached'] or res['limit_reached'] or
                    res['no_records']):
                # Limits reached mean we need to perform a full dump to
                # recover. The same if there is no transaction present,
                # probably due to a pack of the ZODB.
                paths.append('/')
                recurse = True
            else:
                paths.extend(res['paths'])

        # If /a/b as well as /a are to be recorded recursively, drop a/b
        if recurse:
            sync.remove_redundant_paths(paths)

        for path in paths:
            try:
                sync.record(path=path, recurse=recurse)
            except AttributeError:
                print('Unable to record path ' + path)
                pass

        if 'perfact.pfcodechg' in sys.modules and args.commit:
            commit_message = sync.config.commit_message
            # this fails (by design) if no repository is initialized.
            commit_done = perfact.pfcodechg.git_snapshot(
                sync.config.base_dir,
                commit_message,
            )
            # only send a mail if something has changed
            if commit_done and getattr(config, 'codechange_mail', False):
                sync.logger.info('Commit was done! Sending mail...')
                perfact.pfcodechg.git_mail_summary(
                    sync.config.base_dir,
                    sync.config.codechange_mail,
                )

        if args.lasttxn and (newest_txn != lasttxn):
            sync.txn_write(newest_txnid or '')

        sync.release_lock()
