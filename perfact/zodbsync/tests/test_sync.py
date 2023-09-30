import os
import time
import os.path
import base64
import io
import json
import subprocess
import pickle
import pytest
import shutil

import ZEO
import transaction
from AccessControl.SecurityManagement import newSecurityManager
try:  # pragma: no cover
    import ZServer  # noqa: F401
    ZOPE2 = True
except ImportError:  # pragma: no cover
    ZOPE2 = False

try:
    from unittest import mock
except ImportError:
    import mock

from ..main import Runner
from .. import zodbsync
from .. import helpers
from .. import extedit
from .. import object_types
from . import environment as env


class DummyResponse():
    """
    For mocking the request in extedit test
    """
    def __init__(self, app):
        self.headers = {}
        self.app = app

    def __enter__(self):
        self.orig_request = self.app.REQUEST
        self.app.REQUEST = helpers.Namespace(
            _auth='dummy',
            RESPONSE=self,
        )
        return self

    def __exit__(self, *args):
        self.app.REQUEST = self.orig_request

    def setHeader(self, key, value):
        self.headers[key] = value


class TestSync():
    '''
    All tests defined in this class automatically use the environment fixture
    (ZEO, repo etc.)
    '''

    @pytest.fixture(scope='class', autouse=True)
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
        self.run('record', '/')
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', 'init')
        request.cls.initial_commit = self.get_head_id()

        # at this point, the test is called
        yield

        # clean up items
        for item in myenv.values():
            item.cleanup()

    @pytest.fixture(scope='function', autouse=True)
    def envreset(self, request):
        """
        Reset the environment after each test.
        """
        self.run('record', '/')
        # Call test
        yield
        if getattr(self, 'runner', None):
            self.runner.sync.tm.abort()
        cmds = [
            'reset --hard',
            'clean -dfx',
            'checkout master',
            'reset --hard {}'.format(self.initial_commit),
        ]
        for cmd in cmds:
            self.gitrun(*cmd.split())
        output = self.gitoutput('show-ref', '--heads')
        for line in output.strip().split('\n'):
            commit, refname = line.split()
            refname = refname[len('refs/heads/'):]
            if refname != 'master':
                self.gitrun('branch', '-D', refname)

        self.run('playback', '--skip-errors', '/')

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
        tm.abort()
        conn.close()

    def mkrunner(self, *cmd):
        '''
        Create or update runner for given zodbsync command
        '''
        if not hasattr(self, 'runner'):
            self.runner = Runner()
        result = self.runner.parse('--config', self.config.path, *cmd)
        self.app = self.runner.sync.app if self.runner.sync else None
        return result

    def run(self, *cmd):
        "Create runner and run"
        self.mkrunner(*cmd).run()

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

    def upload_checks(self, replace_periods=True, ignore=True):
        '''A bunch of asserts to call after an upload test has been performed
        '''
        assert 'lib' in self.app.objectIds()
        assert 'js' in self.app.lib.objectIds()
        assert 'plugins' in self.app.lib.js.objectIds()
        something_js = 'something_js' if replace_periods else 'something.js'
        assert something_js in self.app.lib.js.plugins.objectIds()
        content = 'alert(1);\n'
        data = helpers.to_string(
            getattr(self.app.lib.js.plugins, something_js).data
        )
        assert content == data

        assert 'css' in self.app.lib.objectIds()
        assert 'skins' in self.app.lib.css.objectIds()
        dark_css = 'dark_css' if replace_periods else 'dark.css'
        assert dark_css in self.app.lib.css.skins.objectIds()
        content = 'body { background-color: black; }\n'
        data = helpers.to_string(
            getattr(self.app.lib.css.skins, dark_css).data
        )
        assert content == data

        # dont forget ignored files!
        if ignore:
            assert 'ignoreme' not in self.app.lib

    def test_record(self):
        '''Recorder tests'''
        # Record everything and make sure acl_users exists
        assert os.path.isfile(
            self.repo.path + '/__root__/acl_users/__meta__'
        )
        # Recording a non-existent object only logs and does not fail
        self.run('record', '/nonexist')
        # Recording with --lasttxn will create the file
        self.run('record', '--lasttxn')
        assert os.path.isfile(os.path.join(self.repo.path, '__last_txn__'))
        # Making a change with a comment indicating the path will make lasttxn
        # pick it up
        tm = self.runner.sync.start_transaction(note='/testpt')
        self.app.manage_addProduct['PageTemplates'].manage_addPageTemplate(
            id='testpt',
            text='test1'
        )
        tm.commit()
        self.run('record', '--lasttxn')
        assert os.path.isdir(os.path.join(self.repo.path, '__root__/testpt'))

    def test_record_commit(self):
        '''Record with --commit (but no mail and no autoreset)'''
        add = (
            self.app.manage_addProduct['PageTemplates'].manage_addPageTemplate
        )
        with self.runner.sync.tm:
            add(id='test', text='test')
        self.run('record', '/', '--commit')
        # Additional run that does no commit since  nothing changed
        self.run('record', '/', '--commit')
        assert os.path.isdir(os.path.join(self.repo.path, '__root__/test'))
        commits = self.gitoutput('log', '--format=%s')
        assert commits == "Generic commit message.\ninit\n"

    def test_record_autoreset(self):
        '''Record with --commit --autoreset.'''
        add = (
            self.app.manage_addProduct['PageTemplates'].manage_addPageTemplate
        )
        with self.runner.sync.tm:
            add(id='test', text='test')
        self.run('record', '/', '--commit', '--autoreset')
        assert not os.path.isdir(os.path.join(self.repo.path, '__root__/test'))
        commits = self.gitoutput('log', '--format=%s')
        assert commits == "init\n"
        assert 'test' not in self.app.objectIds()

    def test_record_unsupported(self):
        """Check that reading /error_log yields an unsupported marker or an
        error."""
        obj = self.runner.sync.app.error_log
        assert 'unsupported' in zodbsync.mod_read(obj)
        with pytest.raises(AssertionError):
            zodbsync.mod_read(obj, onerrorstop=True)

    def test_omit_callable_title(self):
        """It omits title attributes which are callable."""
        app = self.app
        obj = app.manage_addProduct['PageTemplates'].manage_addPageTemplate(
            id='test_pt', title='Not-visible', text='test text')

        def patch_title():
            """Callable to test callable titles."""
            return 'Show-me'

        # Normal case
        result = zodbsync.mod_read(obj)
        assert 'Not-visible' in result['title']

        # with callable title
        with mock.patch.object(obj, 'title', patch_title):
            result = zodbsync.mod_read(obj)
            assert 'title' not in result

    def test_playback(self):
        '''
        Record everything, change /index_html, play it back and check if the
        contents are correct.
        '''
        path = self.repo.path + '/__root__/index_html/__source-utf8__.html'
        content = '<html></html>'
        with open(path, 'w') as f:
            f.write(content)
        self.run('playback', '/index_html')
        assert self.app.index_html() == content

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
        self.run('pick', commit)

        assert 'TestFolder' in self.app.objectIds()

    def test_pick_dryrun(self):
        '''
        Pick a prepared commit in dry-run mode and check that the folder does
        not exist.
        '''
        commit = self.prepare_pick()
        self.run('pick', commit, '--dry-run')

        assert 'TestFolder' not in self.app.objectIds()

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
        self.run('pick', '--grep=T123', commit)

        ids = self.app.objectIds()
        assert 'Test0' in ids
        assert 'Test1' not in ids
        assert 'Test2' in ids

    def test_pick_range(self):
        """
        Prepare three commits and pick them as a range
        """
        for i in range(3):
            self.add_folder('Test' + str(i), 'Commit ' + str(i))
        commit = self.get_head_id()
        self.gitrun('reset', '--hard', 'HEAD~3')
        self.run('pick', 'HEAD..' + commit)
        ids = self.app.objectIds()
        for i in range(3):
            assert 'Test' + str(i) in ids

    def test_pick_fail(self):
        """
        Pick a commit twice, making sure it fails and is rolled back.
        Also pick one applyable and one unknown commit.
        """
        commit = self.prepare_pick()
        for second in [commit, 'unknown']:
            with pytest.raises(subprocess.CalledProcessError):
                self.run('pick', commit, second)
            assert 'TestFolder' not in self.app.objectIds()
            assert not os.path.isdir(self.repo.path + '/__root__/TestFolder')

    def test_upload_relpath(self):
        '''
        Upload JS library from test environment and check for it in Data.fs
        Provide Data.fs path only
        '''

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )

        self.upload_checks()

        # we may even omit __root__ in path!
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )

        self.upload_checks()

        # add another test case showing dot notation also works
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('.', 'lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )

        self.upload_checks()

    def test_upload_options(self):
        '''
        Test upload with different options settings.
        '''
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        self.run(
            'upload',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )
        self.upload_checks(replace_periods=False)

        self.run(
            'upload', '--replace-periods',
            target_jslib_path, target_repo_path
        )
        self.upload_checks(ignore=False)

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', '  ,,css,js, ',
            target_jslib_path, target_repo_path
        )
        self.upload_checks(ignore=True)

        self.run(
            'upload',
            target_jslib_path, target_repo_path
        )
        self.upload_checks(replace_periods=False, ignore=False)

    def test_upload_relpath_fromrepo(self):
        '''
        change working directory to repository before upload to simulate
        calling upload from repo leveraging bash path completion
        '''
        cur_path = os.getcwd()
        os.chdir(self.repo.path)

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('.', '__root__', 'lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )

        self.upload_checks()

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path
        )

        self.upload_checks()

        os.chdir(cur_path)

    def test_upload_dryrun(self):
        '''
        Upload files in dryrun mode, make sure folder is not found in Data.fs
        '''
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join('__root__', 'lib')

        self.run(
            'upload', '--replace-periods',
            '--valid-extensions', 'css,js',
            target_jslib_path, target_repo_path,
            '--dry-run'
        )

        assert 'lib' not in self.app.objectIds()

    def test_emptying_userdefined_roles(self):
        """
        Check fix for #22: if a Folder defines local roles, playback must be
        able to remove them.
        """
        with self.runner.sync.tm:
            self.app._addRole('TestRole')
        self.run('record', '/')
        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            lines = f.readlines()
        with open(fname, 'w') as f:
            f.writelines([line for line in lines if 'TestRole' not in line])
        self.runner.sync.playback_paths(paths=['/'], recurse=False)
        assert self.app.userdefined_roles() == ()

    def test_userdefined_roles_playback(self):
        """
        Test fix #57: Make sure that playback of an object with local roles
        works correctly. Set a local role, record, read out the recording, play
        back, check that it is set correctly, record again and check that the
        recording matches the first one.
        """
        with self.runner.sync.tm:
            self.app._addRole('TestRole')
            self.app.manage_setLocalRoles('perfact', ('TestRole',))
        self.run('record', '/')

        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            recording = f.read()
        self.runner.sync.playback_paths(paths=['/'], recurse=False)
        assert self.app.get_local_roles() == (('perfact', ('TestRole',)),)
        self.runner.sync.record('/', recurse=False)
        with open(fname, 'r') as f:
            assert recording == f.read()

    def test_addprop(self):
        "Add a property to the root object"
        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        prop = {
            'id': 'testprop',
            'type': 'string',
            'value': 'test',
        }
        data['props'] = [list(prop.items())]
        with open(fname, 'w') as f:
            f.write(zodbsync.mod_format(data))
        self.run('playback', '/')
        assert self.app.getProperty('testprop') == 'test'

    def test_addtokenprop(self):
        "Validate tokens are correctly written"
        fname = self.repo.path + '/__root__/__meta__'
        with open(fname, 'r') as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        prop = {
            'id': 'testprop',
            'type': 'tokens',
            'value': ('123', '518'),
        }
        data['props'] = [list(prop.items())]
        with open(fname, 'w') as f:
            f.write(zodbsync.mod_format(data))
        self.run('playback', '/')
        assert self.app.getProperty('testprop') == ('123', '518')

    def test_changeprop(self):
        "Change first the value and then the type of a property"
        with self.runner.sync.tm:
            self.app.manage_addProperty(
                'testprop', 'test', 'string'
            )
        fname = self.repo.path + '/__root__/__meta__'
        self.run('record', '/')
        with open(fname, 'r') as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        for ptype, pval in [('string', 'changed'), ('int', 1)]:
            prop = {
                'id': 'testprop',
                'type': ptype,
                'value': pval,
            }
            data['props'] = [list(prop.items())]
            with open(fname, 'w') as f:
                f.write(zodbsync.mod_format(data))
            self.run('playback', '/')
            assert self.app.getProperty('testprop') == pval
            assert self.app.getPropertyType('testprop') == ptype

    def test_cacheable(self):
        "Add a RamCacheManager and use it for index_html"
        self.app.manage_addProduct[
            'StandardCacheManagers'
        ].manage_addRAMCacheManager(id="http_cache")
        self.app.index_html.ZCacheable_setManagerId("http_cache")
        self.run('record', '/')
        fname = self.repo.path + '/__root__/index_html/__meta__'
        assert "http_cache" in open(fname).read()
        self.run('playback', '/')
        assert self.app.index_html.ZCacheable_getManagerId() == "http_cache"

    def watcher_step_until(self, watcher, cond):
        """
        After we do some changes on the secondary connection for the watcher
        tests, the primary connection might not immediately see the change.
        This helper function checks for a condition with several retries and
        small waiting in between, only failing if the condition keeps being
        false.
        """
        success = False
        for i in range(5):
            watcher.step()
            success = cond()
            if success:
                break
            time.sleep(0.5)
        assert success

    def test_watch_change(self, conn):
        """
        Start the watcher, change something using the second connection without
        commiting yet, do a step on the watcher, make sure the change is not
        yet visible, then commit the change and do another step, making sure
        that it is now present.
        """
        fname = self.repo.path + '/__root__/__meta__'
        watcher = self.mkrunner('watch')
        watcher.setup()
        conn.tm.begin()
        conn.app._addRole('TestRole')
        watcher.step()
        assert 'TestRole' not in open(fname).read()
        conn.tm.commit()
        self.watcher_step_until(watcher,
                                lambda: 'TestRole' in open(fname).read())

    def test_watch_move(self, conn):
        """
        Create a Page Template, record it using the watcher, rename it and make
        sure the watcher notices. Then add a second one and do a
        three-way-rename in one transaction, making sure the watcher keeps
        track.
        """
        watcher = self.mkrunner('watch')
        watcher.setup()
        root = self.repo.path + '/__root__/'
        src = '/__source-utf8__.html'
        app = conn.app

        add = app.manage_addProduct['PageTemplates'].manage_addPageTemplate
        rename = app.manage_renameObject

        with conn.tm:
            add(id='test1', text='test1')
        self.watcher_step_until(watcher,
                                lambda: os.path.isdir(root + 'test1'))

        # Not sure how to apply this specifically to the secondary connection
        # and why it is only needed for the rename and not the adding, but it
        # seems to do the job
        userfolder = conn.app.acl_users
        user = userfolder.getUser('perfact').__of__(userfolder)
        newSecurityManager(None, user)

        with conn.tm:
            rename('test1', 'test2')
        self.watcher_step_until(watcher, lambda: os.path.isdir(root + 'test2'))
        assert not os.path.isdir(root + 'test1')

        with conn.tm:
            add(id='test1', text='test2')
        self.watcher_step_until(watcher, lambda: os.path.isdir(root + 'test1'))

        assert os.path.isdir(root + 'test1')
        assert open(root + 'test1' + src).read() == 'test2'
        assert open(root + 'test2' + src).read() == 'test1'

        with conn.tm:
            rename('test1', 'tmp')
            rename('test2', 'test1')
            rename('tmp', 'test2')
        self.watcher_step_until(
            watcher,
            lambda: open(root + 'test1' + src).read() == 'test1',
        )
        assert open(root + 'test1' + src).read() == 'test1'
        assert open(root + 'test2' + src).read() == 'test2'

    def test_watch_dump_setup(self):
        """
        Check output that a spawned initialization subprocess would generate.
        """
        watcher = self.mkrunner('watch')
        watcher.setup()
        stream = io.BytesIO()
        watcher.dump_setup_data(stream=stream)
        data = pickle.loads(stream.getvalue())
        assert set(data.keys()) == {'tree', 'txn', 'add_oids'}
        tofind = ['/', '/acl_users/', '/index_html/']
        for obj in data['tree'].values():
            if obj['path'] in tofind:
                tofind.remove(obj['path'])
        assert tofind == []

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
        self.run('reset', 'second')
        assert self.app.index_html.title == 'test'

    def test_revert(self):
        """
        Do the same as in test_reset, but afterwards revert it.
        """
        self.test_reset()
        self.run('exec', 'git revert HEAD')
        title = self.app.index_html.title
        assert title != 'test'

    def test_checkout(self):
        """
        Switch to another branch
        """
        self.run('checkout', '-b', 'other')
        # This switches back to master, but with a change
        self.test_reset()
        self.run('checkout', 'other')
        assert self.app.index_html.title != 'test'
        self.run('checkout', 'master')
        assert self.app.index_html.title == 'test'

    def test_exec_checkout(self):
        """
        Prepare two branches and switch between them.
        """
        self.gitrun('branch', 'other')
        self.test_reset()
        self.run('exec', 'git checkout other')
        title = self.app.index_html.title
        assert title != 'test'

    def test_withlock(self):
        "Running with-lock and, inside that, --no-lock, works"
        self.run(
            'with-lock',
            'zodbsync --config {} --no-lock record /'.format(self.config.path),
        )

    def test_extedit(self, encoding=None):
        """
        Update /index_html using the external editor launcher
        """
        header_lines = [
            'url: index_html',
            'path: //index_html',
            'auth: dummy',
            'meta-type: Page Template',
            'content-type: text/html',
        ]
        new_source = 'test'
        with DummyResponse(self.app) as resp:
            # Read control file
            content = extedit.launch(
                self.app,
                self.app.index_html,
                '/index_html',
            )
            headers, orig_source = content.split('\n\n', 1)
            assert headers == '\n'.join(header_lines)
            assert resp.headers['Content-Type'] == (
                'application/x-perfact-zopeedit'
            )

            # Update to new content
            if encoding:
                orig_source, new_source = [
                    helpers.to_string(base64.b64encode(helpers.to_bytes(item)))
                    for item in [orig_source, new_source]
                ]
            res = extedit.launch(
                self.app,
                self.app.index_html,
                '/index_html',
                source=new_source,
                orig_source=orig_source,
                encoding=encoding,
            )
            assert 'success' in res
            assert resp.headers['Content-Type'] == 'application/json'
            assert self.app.index_html._text == 'test'

            # Try the update again, which must fail because the orig_source no
            # longer matches
            res = extedit.launch(
                self.app,
                self.app.index_html,
                '/index_html',
                source=new_source,
                orig_source=orig_source,
                encoding=encoding,
            )
            assert 'error' in json.loads(res)

            # Check for error on invalid path
            res = extedit.launch(
                self.app,
                self.app,
                '/nonexist',
                source='',
                orig_source='',
            )
            assert res == '{"error": "/nonexist not found"}'

    def test_extedit_base64(self):
        self.test_extedit(encoding='base64')

    def test_extedit_binary(self):
        "Test with binary file that is not valid UTF-8"
        self.app.manage_addProduct['OFSP'].manage_addFile(id='blob')
        with DummyResponse(self.app):
            extedit.launch(
                self.app,
                self.app,
                '/blob',
                source=helpers.to_string(base64.b64encode(b'\xff')),
                orig_source='',
                encoding='base64',
            )
            assert self.app.blob.data == b'\xff'

            res = extedit.launch(
                self.app,
                self.app.blob,
                '/blob',
            )
            assert res.endswith('\n\n/w==')

    def meta_file_path(self, *folders):
        """
        takes n folders in order as arguments and returns path to meta file
        """
        path = self.repo.path + '/__root__/'
        for folder in folders:
            path = path + folder + '/'
        path = path + '__meta__'
        return path

    def test_record_structure_and_playback_local_changes(self):
        """
        create structure in zodb and record,
        make local changes in structure, add a local folder, then playback
        and check if changes played back correctly
        """

        # create a structure of folder and sub folder
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"
        self.app.manage_addFolder(id=folder_1)
        self.app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        assert 's_folder_1' in self.app.folder_1.objectIds()

        # record structure and check that the objects are recorded
        self.run('record', '/')
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        # set new title
        path = self.meta_file_path(folder_1, s_folder_1)
        new_title = 'new_title'
        content = "[('title', '"+new_title+"'),('type', 'Folder'),]"
        with open(path, 'w') as f:
            f.write(content)

        # create metadata for new folder
        new_folder = "new_folder"
        path = self.repo.path + \
            '/__root__/'+folder_1+'/'+s_folder_1+'/'+new_folder
        os.mkdir(path)
        with open(path + '/__meta__', 'w') as f:
            f.write('''[
                ('id', '{}'),
                ('title', ''),
                ('type', 'Folder'),
            ]'''.format(new_folder))

        # playback changes and check if they're existent
        self.run('playback', '/')
        assert new_title == self.app.folder_1.s_folder_1.title
        assert new_folder in self.app.folder_1.s_folder_1.objectIds()

    def test_watch_structure_changes_and_playback_local_changes(self, conn):
        """
        create structure while 'watch' command is running,
        add local changes, then play those changes back and check,
        if those changes played back correctly
        """

        # start watch daemon
        watcher = self.mkrunner('watch')
        watcher.setup()
        app = conn.app
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"

        # create folder and wait until watch notices change
        with conn.tm:
            app.manage_addFolder(id=folder_1)
        self.watcher_step_until(watcher,
                                lambda: os.path.isdir(
                                    self.repo.path + '/__root__/'+folder_1))

        # create subfolder and wait until watch notices change
        with conn.tm:
            app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        path = self.repo.path + '/__root__/'+folder_1+'/'+s_folder_1
        self.watcher_step_until(watcher,
                                lambda: os.path.isdir(path))

        # change title
        new_title = "new_title"
        path = self.meta_file_path(folder_1, s_folder_1)
        content = "[('title', '"+new_title+"'),('type', 'Folder'),]"
        with open(path, 'w') as f:
            f.write(content)

        # playback changes and check if those are existent in zodb
        self.run('playback', '/')
        assert new_title == self.app.folder_1.s_folder_1.title

        # wait for watch to notices played back changes
        with open(path) as f:
            meta = f.read()
        self.watcher_step_until(watcher,
                                lambda: "('title', '"+new_title+"')" in meta)

    def test_watch_structure_changes_and_playback_deleted_folder(self, conn):
        """
        create structure while 'watch' command is running,
        remove a folder, then play those changes back and check,
        whether the step function correctly "crashes"
        """

        # start watch daemon
        watcher = self.mkrunner('watch')
        watcher.setup()
        app = conn.app
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"

        # create folder and wait until watch notices change
        with conn.tm:
            app.manage_addFolder(id=folder_1)
        self.watcher_step_until(watcher,
                                lambda: os.path.isdir(
                                    self.repo.path + '/__root__/'+folder_1))

        # create subfolder and wait until watch notices change
        with conn.tm:
            app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        path = self.repo.path + '/__root__/'+folder_1+'/'+s_folder_1
        self.watcher_step_until(watcher,
                                lambda: os.path.isdir(path))

        # remove folder s_folder_1
        shutil.rmtree(path)

        # playback changes and check if those are existent in zodb
        self.run('playback', '/')

        # wait for watch to notices played back changes
        self.watcher_step_until(watcher, watcher.exit.is_set)

    def test_commit_on_branch_and_exec_merge(self):
        '''
        change to a git feature branch and create a
        structure there, commit it and change back to the master branch
        on master branch check if changes from feature arent existent,
        then merge feature branch and check if changes have been applied
        correctly
        '''

        # change to feature branch and commit created folder/ subfolder
        branch = "feature"
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"
        self.run('exec', 'git checkout -b {}'.format(branch))
        self.app.manage_addFolder(id=folder_1)
        self.app.folder_1.manage_addFolder(id=s_folder_1)
        assert s_folder_1 in self.app.folder_1.objectIds()
        self.run('record', '/')
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        self.gitrun('add', '-A')
        self.gitrun('commit', '-m', 'test case 3')

        # checkout to master and check that changes are not yet existent
        self.run('exec', 'git checkout master')
        assert not os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        assert folder_1 not in self.app.objectIds()

        # merge feature branch and check that changes are applied
        self.run('exec', 'git merge {}'.format(branch))
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        assert folder_1 in self.app.objectIds()

    def test_failing_playback_corrupt_metadata(self):
        """
        create a folder in zodb and record it,
        write wrong meta data to the local file system, then playback
        and check if an error occured
        """

        # create new folder and record it
        folder_1 = "folder_1"
        self.app.manage_addFolder(id=folder_1)
        self.run('record', '/')

        # break metadata
        path = self.repo.path + '/__root__/'+folder_1+'/__meta__'
        content = "[('gandalf', 'ThisIsAWrongKey'),]"
        with open(path, 'w') as f:
            f.write(content)

        # test that playback fails
        with pytest.raises(KeyError):
            self.run('playback', '/')

    def test_failing_exec_commands(self):
        """
        call exec commands with wrong commits and
        check if exceptions are thrown correctly
        """
        with pytest.raises(subprocess.CalledProcessError):
            self.run('exec', 'revert ThisIsDefinitelyNoCommit')

        with pytest.raises(subprocess.CalledProcessError):
            self.run('exec', 'reset ThisIsDefinitelyNoCommit')

        with pytest.raises(subprocess.CalledProcessError):
            self.run('exec', 'cherry-pick ThisIsDefinitelyNoCommit')

    def test_create_multiple_commits_on_branch_and_pick_single_on_master(self):
        """
        create a feature branch on which
        two changes will be commited to one commit each
        change back to the master branch and use pick
        to get the changes of that last commit
        make sure only the last changes are present
        """
        branch = "feature"
        self.gitrun('checkout', '-b', branch)

        # make first changes and commit those
        folder_1 = "folder_1"
        self.app.manage_addFolder(id=folder_1)
        assert folder_1 in self.app.objectIds()
        self.run('record', '/')
        assert os.path.isfile(self.meta_file_path(folder_1))
        self.gitrun('add', '-A')
        self.gitrun('commit', '-m', 'pick_commit_1')

        # make second changes and commit those
        folder_2 = "sf_2_tc6"
        self.app.manage_addFolder(id=folder_2)
        assert folder_2 in self.app.objectIds()
        self.run('record', '/')
        assert os.path.isfile(self.meta_file_path(folder_2))
        self.gitrun('add', '-A')
        self.gitrun('commit', '-m', 'pick_commit_2')

        commit = self.get_head_id()

        # checkout master and check both changes aren't existent
        self.run('exec', 'git checkout master')
        assert not os.path.isfile(self.meta_file_path(folder_1))
        assert folder_1 not in self.app.objectIds()
        assert not os.path.isfile(self.meta_file_path(folder_2))
        assert folder_2 not in self.app.objectIds()

        # pick 2nd commit and check that
        # first arent' but second changes are applied
        self.run('pick', commit)
        assert not os.path.isfile(self.meta_file_path(folder_1))
        assert folder_1 not in self.app.objectIds()
        assert os.path.isfile(self.meta_file_path(folder_2))
        assert folder_2 in self.app.objectIds()

    def test_create_structure_and_reset_commits(self):
        """
        create structure in zodb and record,
        make local changes in structure, add a local folder,
        commit these changes then playback
        and check if changes played back correctly
        afterwards reset the last comment and check that changes
        are gone
        """

        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"

        # create first changes and commit those
        self.app.manage_addFolder(id=folder_1)
        self.app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        assert s_folder_1 in self.app.folder_1.objectIds()
        self.run('record', '/')
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))

        self.gitrun('add', '-A')
        self.gitrun('commit', '-m', 'reset_commit_1')

        # create second changes and commit those
        path = self.repo.path + \
            '/__root__/'+folder_1+'/'+s_folder_1+'/__meta__'
        new_title = "new_title"
        content = "[('title', '"+new_title+"'),('type', 'Folder'),]"
        with open(path, 'w') as f:
            f.write(content)
        new_folder = "new_folder"
        path = self.repo.path + \
            '/__root__/'+folder_1+'/'+s_folder_1+'/'+new_folder
        os.mkdir(path)
        with open(path + '/__meta__', 'w') as f:
            f.write('''[
                ('id', '{}'),
                ('title', ''),
                ('type', 'Folder'),
            ]'''.format(new_folder))
        self.run('playback', '/')

        self.gitrun('add', '-A')
        self.gitrun('commit', '-m', 'reset_commit_2')

        # check that changes are existent in zodb
        assert new_title == self.app.folder_1.s_folder_1.title
        assert new_folder in self.app.folder_1.s_folder_1.objectIds()

        # reset HEAD by one commit and check that second changes are
        # not existent anymore but first changes still are
        self.run('reset', 'HEAD~1')
        assert folder_1 in self.app.objectIds()
        assert s_folder_1 in self.app.folder_1.objectIds()
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        assert new_title != self.app.folder_1.s_folder_1.title
        assert new_folder not in self.app.folder_1.s_folder_1.objectIds()

        # reset HEAD by one commit and check that first changes are
        # not existent anymore
        self.run('reset', 'HEAD~1')
        assert folder_1 not in self.app.objectIds()
        assert not os.path.isfile(self.meta_file_path(folder_1))

    @pytest.mark.parametrize('meta_type', object_types.object_handlers)
    def test_objecttypes(self, meta_type):
        """
        Generic test that is executed for each coded object type. This creates
        an object and writes a modification to it, without actually checking
        for anything. Some are known to fail, for example because they need
        products that are not published on pypi or because they need external
        ressources like non-free libraries for external data connections.
        """
        if meta_type in ['DTML TeX', 'ZForce', 'External Method',
                         'Z cxOracle Database Connection',
                         'Z sap Database Connection']:
            pytest.skip("Skipping objects that require elaborate dependencies")

        if 'Test' not in self.app.objectIds():
            self.app.manage_addProduct['OFSP'].manage_addFolder(id='Test')
        if meta_type in ['User Folder', 'Simple User Folder']:
            objid = 'acl_users'
        else:
            objid = 'testobj'
        parent = self.app.Test
        handler = object_types.object_handlers[meta_type]
        # data that is required by some objects and ignored by others
        add_data = {
            'title': 'test',
            'content_type': 'text/plain',
            'connection_id': 'dbconn',
            'connection_string': '',
            'autocommit': False,
            'maxrows': 100,
            'args': '',
            'source': '',
            'smtp_host': 'localhost',
            'smtp_port': '25',
        }
        handler.create(parent, add_data, objid)
        obj = getattr(parent, objid)
        data = zodbsync.mod_read(obj)
        handler.write(obj, data)
        parent.manage_delObjects(ids=[objid])

    def test_ordered_folder_playback(self):
        """
        Checks for the issue recorded in #83: A playback caused by `zodbsync
        exec` that adds a new child to an ordered folder somewhere not at the
        end was still placing it at the end.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct['OFSP'].manage_addOrderedFolder(
                id="Test"
            )
            self.app.Test.manage_addProduct['OFSP'].manage_addFolder(
                id="exist"
            )
        assert self.app.Test.objectIds() == ['exist']
        self.run('record', '/')

        folder = self.repo.path + '/__root__/Test/'

        new_folder = folder + 'new'
        os.mkdir(new_folder)

        with open(os.path.join(new_folder, '__meta__'), 'w') as f:
            f.write(zodbsync.mod_format({
                "title": "",
                "type": "Folder",
            }))

        with open(folder + '__meta__', 'w') as f:
            f.write(zodbsync.mod_format({
                "contents": ["new", "exist"],
                "title": "",
                "type": "Folder (Ordered)",
            }))
        self.run('playback', '--no-recurse', '/Test', '/Test/new')
        assert self.app.Test.objectIds() == ['new', 'exist']

    def test_change_folder_type(self):
        """
        Change a folder to an ordered folder, but without having all children
        in the contents field. The named children must be in the correct order.
        Also check that children are not unnecessarily deleted and recreated by
        a type change.
        Afterwards, change back to Folder and again check that the children
        stay the same.
        Also change the type of a folder without children.
        """
        def add(parent, fid):
            parent.manage_addProduct['OFSP'].manage_addFolder(id=fid)

        with self.runner.sync.tm:
            add(self.app, 'Test')
            for child in ['A', 'B', 'C']:
                add(self.app.Test, child)
        self.run('record', '/')
        meta = '{}/__root__/Test/__meta__'.format(self.repo.path)

        with open(meta, 'w') as f:
            f.write(zodbsync.mod_format({
                'contents': ['B', 'A'],
                'title': 'change',
                'type': 'Folder (Ordered)',
            }))
        orig_oid = self.app.Test.A._p_oid
        self.run('playback', '/Test', '--override')
        assert self.app.Test.meta_type == 'Folder (Ordered)'
        ids = self.app.Test.objectIds()
        assert sorted(ids) == ['A', 'B', 'C']
        assert ids.index('B') < ids.index('A')
        assert self.app.Test.A._p_oid == orig_oid

        with open(meta, 'w') as f:
            f.write(zodbsync.mod_format({
                'title': 'change again',
                'type': 'Folder',
            }))
        self.run('playback', '/Test', '--override')
        assert self.app.Test.meta_type == 'Folder'
        assert sorted(self.app.Test.objectIds()) == ['A', 'B', 'C']
        assert self.app.Test.A._p_oid == orig_oid

        with self.runner.sync.tm:
            self.app.Test.manage_delObjects(ids=['A', 'B', 'C'])
        self.run('record', '/')
        with open(meta, 'w') as f:
            f.write(zodbsync.mod_format({
                'title': 'change',
                'type': 'Folder (Ordered)',
            }))
        self.run('playback', '/Test', '--override')
        assert self.app.Test.meta_type == 'Folder (Ordered)'

    def test_create_userfolder(self):
        """
        Check that we can recover from a state where the top-level userfolder
        was deleted.
        Note that we here call create_manager_user manually, but this is not
        necessary when using `zodbsync playback` since it is called upon
        initialization of the `ZODBSync` class instance if the config variable
        is set accordingly. But since the test tries to avoid tearing down and
        recreating the class instance, we need to call it manually.
        """
        with self.runner.sync.tm:
            self.app.manage_delObjects('acl_users')
            self.runner.sync.create_manager_user()
        self.run('playback', '/')
        assert self.app.acl_users.meta_type == 'User Folder'

    def test_no_unnecessary_writes(self):
        """
        Check that recording or playing back an unchanged object does not
        actually update it.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct['OFSP'].manage_addFolder(id='test')

        folder = self.app.test
        mtime1 = folder._p_mtime

        self.run('record', '/test')
        self.run('playback', '/test')
        mtime2 = folder._p_mtime
        assert mtime1 == mtime2

        path = self.repo.path + '/__root__/test/__meta__'
        fsmtime1 = os.stat(path).st_mtime
        self.run('record', '/test')
        fsmtime2 = os.stat(path).st_mtime
        assert fsmtime1 == fsmtime2

    def test_no_meta_file(self):
        """
        Check that a missing meta file is detected and we run into an error
        """

        broken_obj = os.path.join(self.repo.path, '__root__', 'foo')
        os.mkdir(broken_obj)

        with pytest.raises(AssertionError):
            self.run('playback', '/foo')

    def test_force_default_owner(self):
        """
        Check if the default owner can be forced via config
        """

        self.runner.sync.force_default_owner = True

        # first test: owner from meta file pushed to app
        folder = os.path.join(self.repo.path, '__root__', 'newfolder')
        os.mkdir(folder)

        with open(os.path.join(folder, '__meta__'), 'w') as f:
            f.write(zodbsync.mod_format({
                "title": "",
                "type": "Folder",
                "owner": (['acl_users'], "Somebody"),
            }))

        self.run('playback', '/newfolder')

        expected_owner = (['acl_users'], self.runner.sync.default_owner)

        assert self.app.newfolder._owner == expected_owner

        # second test: owner from zope read to meta file
        with self.runner.sync.tm:
            self.app.manage_addProduct['OFSP'].manage_addFolder(id='another')

        self.app.another._owner = (['acl_users'], "Somebody")

        self.run('record', '/')

        meta = self.runner.sync.fs_read('another')

        assert 'owner' not in meta

    def test_force_default_owner_negative(self):
        """
        Negative test for force_default_owner setting: Make sure we see
        to old behaviour without this setting being set
        """

        self.runner.sync.force_default_owner = False

        # first test: owner from meta file pushed to app
        folder = os.path.join(self.repo.path, '__root__', 'newfolder')
        os.mkdir(folder)

        with open(os.path.join(folder, '__meta__'), 'w') as f:
            f.write(zodbsync.mod_format({
                "title": "",
                "type": "Folder",
                "owner": (['acl_users'], "Somebody"),
            }))

        self.run('playback', '/newfolder')
        assert self.app.newfolder._owner == (['acl_users'], "Somebody")

        # second test: owner from zope read to meta file
        with self.runner.sync.tm:
            self.app.manage_addProduct['OFSP'].manage_addFolder(id='another')

        self.app.another._owner = (['acl_users'], "Somebody")

        self.run('record', '/')

        meta = self.runner.sync.fs_read('another')

        assert meta['owner'] == (['acl_users'], "Somebody")

    def test_reformat(self):
        """
        Make a couple of commits with changes to a meta file using the legacy
        format. Then reformat them, checking that no error occurs and that the
        final state uses the new formatting.
        """
        folder = os.path.join(self.repo.path, '__root__/Test')
        os.mkdir(folder)
        fname = os.path.join(folder, '__meta__')

        def commit():
            self.gitrun('add', '__root__/Test/__meta__')
            self.gitrun('commit', '-m', 'Test')

        def store(data, strip=False):
            # With strip=False, simulate an older version where there was no
            # newline at the end of meta files
            with open(fname, 'w') as f:
                s = helpers.StrRepr()(data, legacy=True)
                if strip:
                    s = s.strip()
                f.write(s)
            commit()

        store({
            'title': 'Zope',
            'roles': ['A'],
            'perms': [('View', False, ['Anonymous'])],
        })
        start = self.get_head_id()

        store({
            'title': 'Other',
            'roles': ['A', 'B'],
            'perms': [('View', True, ['Anonymous', 'A'])],
        }, strip=True)

        # Add a commit that deletes the object while it does not end in a
        # newline. A naive cherry-pick would result in a merge conflict.
        shutil.rmtree(folder)
        commit()
        os.mkdir(folder)

        store({
            'title': 'Other',
            'props': [
                [('id', 'columns'), ('type', 'tokens'),
                 ('value', ('a', 'b', 'c'))],
            ]
        })

        self.run('reformat', start)
        with open(fname) as f:
            fmt = f.read()
        assert fmt.strip().split('\n') == [
            "[",
            "    ('props', [",
            "        [('id', 'columns'), ('type', 'tokens'), ('value', (",
            "            'a',",
            "            'b',",
            "            'c',",
            "        ))],",
            "    ]),",
            "    ('title', 'Other'),",
            "]",
        ]

    def test_replace_child_by_property(self):
        """
        Test that it is possible to remove a child and add a property with the
        same name in the same transaction, and also vice versa.
        """
        with self.runner.sync.tm:
            self.app._setProperty('test', 'foo', 'string')

        self.run('record', '/')
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', 'with property')
        c1 = self.get_head_id()

        with self.runner.sync.tm:
            self.app.manage_delProperties(ids=['test'])
            self.app.manage_addProduct['OFSP'].manage_addFolder(id='test')

        self.run('record', '/')
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', 'with child')
        c2 = self.get_head_id()

        self.run('reset', c2)
        self.run('reset', c1)

    def test_playback_postprocess(self):
        """
        Add configuration option for a postprocessing script and check that
        zodbsync reset executes it.
        """
        fname = "{}/postproc".format(self.zeo.path)
        outfile = "{}.out".format(fname)
        script = '\n'.join([
            "#!/bin/bash",
            "cat > {}",
        ]).format(outfile)
        with open(fname, 'w') as f:
            f.write(script)
        os.chmod(fname, 0o700)
        with open(self.config.path) as f:
            orig_config = f.read()
        with open(self.config.path, 'a') as f:
            f.write('\nrun_after_playback = "{}"\n'.format(fname))

        # Avoid error regarding reusing runner with changed config
        del self.runner
        self.test_reset()
        with open(outfile) as f:
            assert json.loads(f.read()) == {"paths": ["/index_html/"]}

        with open(self.config.path, 'w') as f:
            f.write(orig_config)
        del self.runner
