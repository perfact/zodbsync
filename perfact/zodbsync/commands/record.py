#!/usr/bin/env python

import smtplib
import subprocess
import argparse
from email.mime.text import MIMEText

from ..subcommand import SubCommand
from .reset import Reset


class Record(SubCommand):
    '''Record objects from the Data.FS to the file system'''
    @staticmethod
    def add_args(parser):
        parser.add_argument(
            '--lasttxn', action='store_true', default=False,
            help='Add paths mentioned in transactions since the last used',
        )
        parser.add_argument(
            '--commit', action='store_true', default=False,
            help='Commit changes and send summary mail if there are any',
        )
        parser.add_argument(
            '--autoreset', action='store_true', default=False,
            help='Automatically reset changes after sending mail for --commit',
        )
        parser.add_argument(
            '--no-recurse', action='store_true', default=False,
            help='Record only specified paths without recursing',
        )
        parser.add_argument(
            '--skip-errors', action='store_true',
            help="Skip failed objects and continue",
            default=False
        )
        parser.add_argument(
            'path', type=str, nargs='*',
            help='Sub-Path in Data.fs to be recorded',
        )

    def commit(self):
        """
        Do a commit of all unstaged changes and optionally send an email with a
        summary.
        """
        commit_message = self.config["commit_message"]
        self.gitcmd_run('add', '.')
        try:
            self.gitcmd_run('commit', '-m', commit_message)
        except subprocess.CalledProcessError:
            # Nothing to commit
            return

        # only send a mail if something has changed
        codechg_mail = self.config.get('codechange_mail', False)
        if codechg_mail:  # pragma: no cover
            self.logger.info('Commit was done! Sending mail...')
            pfsystemid = open('/etc/pfsystemid').read().strip()
            pfsystemname = open('/etc/pfsystemname').read().strip()

            status = self.gitcmd_output('show', '--name-status', 'HEAD')

            msg = MIMEText(status, 'plain', 'utf-8')
            msg['Subject'] = 'Commit summary on {} ({})'.format(pfsystemname,
                                                                pfsystemid)

            recipients = codechg_mail.split()
            for recipient in recipients:
                msg['To'] = recipient

            msg['From'] = self.config.get('codechange_sender',
                                          'codechanges@perfact.de')

            smtp = smtplib.SMTP('localhost')
            smtp.sendmail(msg['From'], recipients, msg.as_string())
            smtp.quit()

        if self.args.autoreset:
            reset = Reset(sync=self.sync, logger=self.logger,
                          config=self.config)
            parser = argparse.ArgumentParser()
            parser.add_argument('--no-lock', action='store_true')
            reset.add_args(parser)
            reset.args = parser.parse_args(['--no-lock', 'HEAD~'])
            reset.run()

    @SubCommand.with_lock
    def run(self):
        paths = self.args.path
        recurse = not self.args.no_recurse

        if self.args.lasttxn:
            # We mean to read from the newest entry
            lasttxn = self.sync.txn_read() or None

            res = self.sync.recent_changes(
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

        self.sync.record(paths=paths, recurse=recurse,
                         skip_errors=self.args.skip_errors)

        if self.args.commit:
            self.commit()

        if self.args.lasttxn and (newest_txnid != lasttxn):
            self.sync.txn_write(newest_txnid or '')
