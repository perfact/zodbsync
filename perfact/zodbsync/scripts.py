import argparse

from perfact.zodbsync.main import Runner
try:
    # psql dump for backwards compatibility
    import perfact.dbbackup
except ImportError:
    pass


def zodbsync():
    Runner().run()


def zoperecord():
    parser = argparse.ArgumentParser(
        description='Record the Data.fs',
        epilog='''This script is deprecated in favor of zodbsync. Only bare
        functionality is provided for backwards compatibility with existing
        cron entries.
        '''
    )
    parser.add_argument('--lasttxn', action='store_true', default=False,
                        help='Record only transactions since the last used.')

    args = parser.parse_args()
    if args.lasttxn:
        cmd = 'record --lasttxn'
    else:
        cmd = 'record --commit /'

    runner = Runner().parse(*cmd.split())

    # dump tables and schemas if run without --lasttxn and if the corresponding
    # variables are found in the config - this is only for backwards
    # compatibility, this should be done by perfact-dbrecord instead.
    config = runner.config
    databases = getattr(config, 'databases', None)
    if not args.lasttxn and databases is not None:
        runner.logger.warn(
            'Deprecation warning: dumping PostgreSQL schema and tables, which'
            ' should be done by perfact-dbrecord instead.'
        )
        msgbak = config.commit_message
        config.commit_message += ' (Database)'
        perfact.dbbackup.git_snapshot(config)
        config.commit_message = msgbak

    runner.run()


def zopeplayback():
    assert False, (
        "perfact-zopeplayback is no longer supported as executable, please use"
        " zodbsync instead."
    )
