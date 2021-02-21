import os
import os.path
import subprocess
import pytest

import ZEO
import transaction
from AccessControl.SecurityManagement import newSecurityManager
try:  # pragma: no cover
    from Zope2.Startup.run import configure  # noqa: F401
    ZOPE2 = True
except ImportError:
    ZOPE2 = False

from ..main import Runner
from .. import helpers
from . import environment as env


class TestSync():
    '''
    All tests defined in this class automatically use the environment fixture
    (ZEO, repo etc.)
    '''

    @pytest.fixture(scope='function', autouse=True)
    def environment(self, request):
        '''
        Fixture that is automatically used by all tests. Initializes
        environment and injects the elements of it into the class.
        '''
        myenv = {}
        myenv['zeo'] = env.ZeoInstance()
        myenv['repo'] = env.Repository()
        myenv['zopeconfig'] = env.ZopeConfig(zeosock=myenv['zeo'].sockpath(),
                                             add_tempstorage=ZOPE2)
        myenv['jslib'] = env.JSLib()
        myenv['config'] = env.ZODBSyncConfig(env=myenv)

        # inject items into class so methods can use them
        for key, value in myenv.items():
            setattr(request.cls, key, value)

        # Initially record everything and commit it
        self.runner('record', '/').run()
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', 'init')

        # at this point, the test is called
        yield

        # clean up items
        for item in myenv.values():
            item.cleanup()

    @pytest.fixture(scope='function')
    def conn(self, request):
        """
        Fixture that provides a secondary connection to the same ZEO
        """
        tm = transaction.TransactionManager()
        db = ZEO.DB(self.zeo.sockpath())
        conn = db.open(tm)
        app = conn.root.Application

        yield helpers.Namespace({'tm': tm, 'app': app})
        conn.close()

    def runner(self, *cmd):
        '''
        Create runner for given zodbsync command
        '''
        return Runner().parse('--config', self.config.path, *cmd)

    def gitrun(self, *cmd):
        '''
        Run git command.
        '''
        subprocess.check_call(
            ['git', '-C', self.repo.path] + list(cmd)
        )

    def gitoutput(self, *cmd):
        '''
        Run git command, returning output.
        '''
        return subprocess.check_output(
            ['git', '-C', self.repo.path] + list(cmd),
            universal_newlines=True,
        )

    def upload_checks(self, runner):
        '''A bunch of asserts to call after an upload test has been performed
        '''
        assert 'lib' in runner.sync.app.objectIds()
        assert 'js' in runner.sync.app.lib.objectIds()
        assert 'plugins' in runner.sync.app.lib.js.objectIds()
        assert 'something_js' in runner.sync.app.lib.js.plugins.objectIds()
        content = 'alert(1);\n'
        data = helpers.to_string(
            runner.sync.app.lib.js.plugins.something_js.data
        )
        assert content == data

        assert 'css' in runner.sync.app.lib.objectIds()
        assert 'skins' in runner.sync.app.lib.css.objectIds()
        assert 'dark_css' in runner.sync.app.lib.css.skins.objectIds()
        content = 'body { background-color: black; }\n'
        data = helpers.to_string(
            runner.sync.app.lib.css.skins.dark_css.data
        )
        assert content == data

        # dont forget ignored files!
        assert 'ignoreme' not in runner.sync.app.lib

    def test_record(self):
        '''
        Record everything and make sure acl_users exists.
        '''
        assert os.path.isfile(
            self.repo.path + '/__root__/acl_users/__meta__'
        )

    def test_playback(self):
        '''
        Record everything, change /index_html, play it back and check if the
        contents are correct.
        '''
        path = self.repo.path + '/__root__/index_html/__source-utf8__.html'
        content = '<html></html>'
        with open(path, 'w') as f:
            f.write(content)
        runner = self.runner('playback', '/index_html')
        runner.run()
        assert runner.sync.app.index_html() == content

    def add_folder(self, name, msg):
        """
        Add a folder to the root directory and commit it
        """
        folder = self.repo.path + '/__root__/' + name
        os.mkdir(folder)
        with open(folder + '/__meta__', 'w') as f:
            f.write('''[
                ('props', []),
                ('title', ''),
                ('type', 'Folder'),
            ]''')
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', msg)

    def get_head_id(self):
        """Return commit ID of current HEAD."""
        return self.gitoutput('show-ref', '--head', '--hash', 'HEAD').strip()

    def prepare_pick(self, name='TestFolder', msg='Second commit'):
        '''
        Prepare a commit containing a new folder that can be picked onto the
        initialized repository. Returns the commit ID.
        '''
        # Add a folder, commit it
        self.add_folder('TestFolder', 'Second commit')
        commit = self.get_head_id()

        # Reset the commit
        self.gitrun('reset', '--hard', 'HEAD~')

        return commit

    def test_pick(self):
        '''
        Pick a prepared commit and check that the folder exists.
        '''
        commit = self.prepare_pick()
        runner = self.runner('pick', commit)
        runner.run()

        assert 'TestFolder' in runner.sync.app.objectIds()

    def test_pick_dryrun(self):
        '''
        Pick a prepared commit in dry-run mode and check that the folder does
        not exist.
        '''
        commit = self.prepare_pick()
        runner = self.runner('pick', commit, '--dry-run')
        runner.run()

        assert 'TestFolder' not in runner.sync.app.objectIds()

    def test_pick_grep(self):
        """
        Prepare three commits where the first and third share a common pattern
        in the commit message, then pick only those.
        """
        msgs = [
            'T123: first commit',
            'T456: second commit',
            'T123: third commit',
        ]
        for nr, msg in enumerate(msgs):
            self.add_folder('Test' + str(nr), msg)
        commit = self.get_head_id()
        self.gitrun('reset', '--hard', 'HEAD~3')
        runner = self.runner('pick', '--grep=T123', commit)
        runner.run()

        ids = runner.sync.app.objectIds()
        assert 'Test0' in ids
        assert 'Test1' not in ids
        assert 'Test2' in ids

    def test_upload_relpath(self):
        '''
        Upload JS library from test environment and check for it in Data.fs
        Provide Data.fs path only
        '''

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        runner = self.runner('upload', target_jslib_path, target_repo_path)
        runner.run()

        self.upload_checks(runner)

        # we may even omit __root__ in path!
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('lib')

        runner = self.runner('upload', target_jslib_path, target_repo_path)
        runner.run()

        self.upload_checks(runner)

        # add another test case showing dot notation also works
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('.', 'lib')

        runner = self.runner('upload', target_jslib_path, target_repo_path)
        runner.run()

        self.upload_checks(runner)

    def test_upload_relpath_fromrepo(self):
        '''
        change working directory to repository before upload to simulate
        calling upload from repo leveraging bash path completion
        '''
        cur_path = os.getcwd()
        os.chdir(self.repo.path)

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('.', '__root__', 'lib')

        runner = self.runner('upload', target_jslib_path, target_repo_path)
        runner.run()

        self.upload_checks(runner)

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        runner = self.runner('upload', target_jslib_path, target_repo_path)
        runner.run()

        self.upload_checks(runner)

        os.chdir(cur_path)

    def test_upload_dryrun(self):
        '''
        Upload files in dryrun mode, make sure folder is not found in Data.fs
        '''
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        runner = self.runner(
            'upload', target_jslib_path, target_repo_path, '--dry-run'
        )
        runner.run()

        assert 'lib' not in runner.sync.app.objectIds()

    def test_emptying_userdefined_roles(self):
        """
        Check fix for #22: if a Folder defines local roles, playback must be
        able to remove them.
        """
        runner = self.runner('record', '/')
        runner.sync.app._addRole('TestRole')
        runner.run()
        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            lines = f.readlines()
        with open(fname, 'w') as f:
            f.writelines([line for line in lines if 'TestRole' not in line])
        runner.sync.playback_paths(paths=['/'], recurse=False)
        assert runner.sync.app.userdefined_roles() == ()

    def test_userdefined_roles_playback(self):
        """
        Test fix #57: Make sure that playback of an object with local roles
        works correctly. Set a local role, record, read out the recording, play
        back, check that it is set correctly, record again and check that the
        recording matches the first one.
        """
        runner = self.runner('record', '/')
        app = runner.sync.app
        app._addRole('TestRole')
        app.manage_setLocalRoles('perfact', ('TestRole',))
        runner.run()

        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            recording = f.read()
        runner.sync.playback_paths(paths=['/'], recurse=False)
        assert app.get_local_roles() == (('perfact', ('TestRole',)),)
        runner.sync.record('/', recurse=False)
        with open(fname, 'r') as f:
            assert recording == f.read()

    def test_watch_change(self, conn):
        """
        Start the watcher, change something using the second connection without
        commiting yet, do a step on the watcher, make sure the change is not
        yet visible, then commit the change and do another step, making sure
        that it is now present.
        """
        fname = self.repo.path + '/__root__/__meta__'
        watcher = self.runner('watch')
        watcher.setup()
        conn.tm.begin()
        conn.app._addRole('TestRole')
        watcher.step()
        assert 'TestRole' not in open(fname).read()
        conn.tm.commit()
        watcher.step()
        assert 'TestRole' in open(fname).read()

    def test_watch_move(self, conn):
        """
        Create a Page Template, record it using the watcher, rename it and make
        sure the watcher notices. Then add a second one and do a
        three-way-rename in one transaction, making sure the watcher keeps
        track.
        """
        watcher = self.runner('watch')
        watcher.setup()
        root = self.repo.path + '/__root__/'
        src = '/__source-utf8__.html'
        app = conn.app

        add = app.manage_addProduct['PageTemplates'].manage_addPageTemplate
        rename = app.manage_renameObject

        with conn.tm:
            add(id='test1', text='test1')
        watcher.step()
        assert os.path.isdir(root + 'test1')

        # Not sure how to apply this specifically to the secondary connection
        # and why it is only needed for the rename and not the adding, but it
        # seems to do the job
        newSecurityManager(None, conn.app.acl_users.getUserById('perfact'))

        with conn.tm:
            rename('test1', 'test2')
        watcher.step()
        assert os.path.isdir(root + 'test2')
        assert not os.path.isdir(root + 'test1')

        with conn.tm:
            add(id='test1', text='test2')
        watcher.step()
        assert os.path.isdir(root + 'test1')
        assert open(root + 'test1' + src).read() == 'test2'
        assert open(root + 'test2' + src).read() == 'test1'

        with conn.tm:
            rename('test1', 'tmp')
            rename('test2', 'test1')
            rename('tmp', 'test2')
        watcher.step()
        assert open(root + 'test1' + src).read() == 'test1'
        assert open(root + 'test2' + src).read() == 'test2'

    def test_reset(self):
        """
        Change the title of index_html in a second branch, reset to it and
        check that it is played back correctly.
        """
        self.gitrun('checkout', '-b', 'second')
        path = self.repo.path + '/__root__/index_html/__meta__'
        with open(path) as f:
            lines = f.readlines()
        lines = [
            line if "('title', " not in line
            else "    ('title', 'test'),\n"
            for line in lines
        ]
        with open(path, 'w') as f:
            f.writelines(lines)
        self.gitrun('commit', '-a', '-m', 'Change title')
        self.gitrun('checkout', 'master')
        runner = self.runner('reset', 'second')
        runner.run()
        assert runner.sync.app.index_html.title == 'test'
