# Path of the Zope instance configuration to use to instantiate the application
# object
conf_path = '/var/lib/zope4/ema/etc/zope.conf'

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

# use default owner even if we're told otherwise by meta file
force_default_owner = False

# Base directory of the repository
base_dir = '/opt/perfact/dbutils-zoperepo'

# default settings for git repos
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Generic commit message."

# email address to send commit summaries of default commits to
#codechange_mail = "zope-devel@example.de"
#codechange_sender = "no-reply-zodbsync-changes@example.de"

# Path to script which is called to define the phases of playback to be
# executed.
# playback_hook = '/usr/share/perfact/zope4-tools/zodbsync-playback-hook'

# Path to script that is called for postprocessing after a playback if it exists
# run_after_playback = '/usr/share/perfact/zope4-tools/zodbsync-postproc'
