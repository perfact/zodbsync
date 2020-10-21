import os
import tempfile
import shutil
import subprocess

'''
These classes together define an environment containing of a running ZEO
instance and a git repository (both running in temporary directories) as well
as a Zope instance configuration connecting to this ZEO and a ZODBSync
configuration connecting everything. They are used by the fixture defined in
conftest.py to provide an environment for the tests.
'''


class ZeoInstance():
    def __init__(self, port):
        self.path = tempfile.mkdtemp()
        subprocess.check_call(
            ['mkzeoinstance', self.path, '127.0.0.1:%d' % port]
        )
        self.zeo = subprocess.Popen([self.path + '/bin/runzeo'])

    def cleanup(self):
        self.zeo.terminate()
        self.zeo.wait()
        shutil.rmtree(self.path)


class Repository():
    def __init__(self):
        self.path = tempfile.mkdtemp()
        commands = [
            ['init'],
            ['config', 'user.email', 'test@zodbsync.org'],
            ['config', 'user.name', 'testrepo'],
        ]
        for cmd in commands:
            subprocess.check_call(['git', '-C', self.path] + cmd)

    def cleanup(self):
        shutil.rmtree(self.path)


class ZopeConfig():
    def __init__(self, zeoport):
        self.path = tempfile.mkdtemp()
        self.config = self.path + '/zope.conf'
        with open(self.config, 'w') as f:
            f.write('''
%define INSTANCE {path}
%define ZEO_SERVER 127.0.0.1:{port}

instancehome $INSTANCE

<zodb_db main>
    <zeoclient>
      server $ZEO_SERVER
      storage 1
      name zeostorage
      var $INSTANCE/var
      cache-size 20MB
    </zeoclient>
   mount-point /
</zodb_db>
            '''.format(port=zeoport, path=self.path)
                    )

    def cleanup(self):
        shutil.rmtree(self.path)


class ZODBSyncConfig():
    def __init__(self, env):
        _, self.path = tempfile.mkstemp()
        with open(self.path, 'w') as f:
            f.write('''
# Path of the Zope instance configuration to use to
# instantiate Zope2.app()
wsgi_conf_path = '{zopeconf}'

# Path to Data.fs which is needed for lookup of object IDs from transaction IDs
# with perfact-zoperecord --watch
datafs_path = '{zeopath}/var/Data.fs'

# user that is used to create commits and as default owner of objects
manager_user = 'perfact'

# create the manager user on empty databases
create_manager_user = True

# sets the default owner for objects that have no owner in the file system
# representation
default_owner = 'perfact'

# Base directory of the repository
base_dir = '{repodir}'

# default settings for git repos
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Generic commit message."
            '''.format(
                zopeconf=env['zopeconfig'].config,
                zeopath=env['zeo'].path,
                repodir=env['repo'].path,
            ))

    def cleanup(self):
        os.remove(self.path)


class JSLib():
    '''
    A test JS library containing some JS and CSS files
    '''
    def __init__(self):
        self.path = tempfile.mkdtemp()

        self.js_folder = os.path.join(self.path, 'js', 'plugins')
        os.makedirs(self.js_folder)
        with open(os.path.join(self.js_folder, 'something.js'), 'w') as jsfile:
            jsfile.write('alert(1);\n')

        self.css_folder = os.path.join(self.path, 'css', 'skins')
        os.makedirs(self.css_folder)
        with open(os.path.join(self.css_folder, 'dark.css'), 'w') as cssfile:
            cssfile.write('body { background-color: black; }\n')

        with open(os.path.join(self.path, 'ignoreme'), 'w') as ignorefile:
            ignorefile.write('something to ignore')

    def cleanup(self):
        shutil.rmtree(self.path)
