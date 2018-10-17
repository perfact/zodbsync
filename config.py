# Path of the Zope instance configuration to use to
# instantiate Zope2.app()
conf_path = '/var/lib/zope2.13/instance/ema/etc/zope.conf'

# Path to Data.fs which is needed for lookup of object IDs from transaction IDs
# with perfact-zoperecord --watch
datafs_path = '/var/lib/zope2.13/zeo/emazeo/var/Data.fs'

# user that is used to create commits and as default owner of objects
manager_user = 'perfact'

# create the manager user on empty databases
create_manager_user = False

# Base directory of the repository
base_dir = '/opt/perfact/dbutils-zoperepo'

# Databases to store (usually only one)
databases = ['perfactema', ]

# Tables to dump on a regular basis
db_tables = {
    'perfactema': [
        'apppref',
        'appnavcustom',
        'appcerttype',
        'datadict',
        'datadictint',
        'dbcleanup',
        'country',
        'escalbasemethod',
        'escalmethod',
        'i18nlang',
        'i18nlex',
        #'i18nelem',
        #'i18ntrans',
        'qnattrtype',
        # module SCA
        'filestatus',
        'filetype',
        'gentemplate',
        # module UCA
        'cachereqlc',
        'xferstatus',
        # module PSA
        'ean128',
        'appdevtype',
        'appdevtgt',
        'brserv',
        'brservitem',
        'i18nelem',
        'i18ntrans',
    ],
}

# default settings for git repos
commit_name = "Zope Developer"
commit_email = "zope-devel@perfact.de"
commit_message = "Generic commit message."

# email address to send commits to
#codechange_mail = "pfcodechange-test@perfact.de"
