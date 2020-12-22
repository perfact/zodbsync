# Path of the Zope instance configuration to use to instantiate the application
# object
wsgi_conf_path = '/var/lib/zope4/ema/etc/zope.conf'

# Path to Data.fs which is needed for lookup of object IDs from transaction IDs
# with zodbsync watch
datafs_path = '/var/lib/zope4/zeo/var/Data.fs'

# user that is used to create commits
manager_user = 'perfact'

# create the manager user with a default password if not present
create_manager_user = True

# sets the default owner for objects that have no owner in the file system
# representation
default_owner = 'perfact'

# Base directory of the repository
base_dir = '/opt/perfact/dbutils-zoperepo'

# default settings for git repos
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Generic commit message."

# email address to send commit summaries of default commits to
#codechange_mail = "zope-devel@example.de"
