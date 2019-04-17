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

# sets the default owner for objects that have no owner in the file system representation
default_owner = 'perfact'

# Base directory of the repository
base_dir = '/opt/perfact/dbutils-zoperepo'

# default settings for git repos
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Generic commit message."

# email address to send commit summaries of default commits to
#codechange_mail = "zope-devel@example.de"
