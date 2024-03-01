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
    def __init__(self):
        self.path = tempfile.mkdtemp()
        subprocess.check_call(['mkzeoinstance', self.path])

        # replace address line to use a socket
        fname = self.path + '/etc/zeo.conf'
        with open(fname) as f:
            lines = f.readlines()
        subst = '  address ' + self.sockpath() + '\n'
        lines = [
            subst if '  address' in line else line
            for line in lines
        ]
        with open(fname, 'w') as f:
            f.writelines(lines)

        self.zeo = subprocess.Popen([self.path + '/bin/runzeo'])

    def sockpath(self):
        return self.path + '/var/zeo.sock'

    def cleanup(self):
        self.zeo.terminate()
        self.zeo.wait()
        shutil.rmtree(self.path)


class Repository():
    def __init__(self):
        self.path = tempfile.mkdtemp()
        commands = [
            ['init'],
            ['branch', '-m', 'autotest'],
            ['config', 'user.email', 'test@zodbsync.org'],
            ['config', 'user.name', 'testrepo'],
        ]
        for cmd in commands:
            subprocess.check_call(['git', '-C', self.path] + cmd)

    def cleanup(self):
        shutil.rmtree(self.path)


class ZopeConfig():
    def __init__(self, zeosock):
        self.path = tempfile.mkdtemp()
        self.config = self.path + '/zope.conf'
        content = '''
%define INSTANCE {path}
%define ZEO_SERVER {zeosock}

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
        '''.format(zeosock=zeosock, path=self.path)

        with open(self.config, 'w') as f:
            f.write(content)

    def cleanup(self):
        shutil.rmtree(self.path)


class ZODBSyncConfig():
    def __init__(self, env):
        self.folder = tempfile.mkdtemp()
        os.mkdir(self.folder + '/layers')
        self.path = self.folder + '/zodb.py'
        with open(self.path, 'w') as f:
            f.write('''
conf_path = '{zopeconf}'
datafs_path = '{zeopath}/var/Data.fs'
manager_user = 'perfact'
create_manager_user = True
default_owner = 'perfact'
base_dir = '{repodir}'
commit_name = "Zope Developer"
commit_email = "zope-devel@example.de"
commit_message = "Generic commit message."
layers = "{root}/layers"
            '''.format(
                zopeconf=env['zopeconfig'].config,
                zeopath=env['zeo'].path,
                repodir=env['repo'].path,
                root=self.folder,
            ))

    def cleanup(self):
        shutil.rmtree(self.folder)


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
