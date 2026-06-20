import base64
import io
import json
import os
import os.path
import pickle
import random
import shutil
import string
import subprocess
import tempfile
import time
from contextlib import contextmanager

import pytest
import transaction
import ZEO
from AccessControl.SecurityManagement import newSecurityManager

try:
    from unittest import mock
except ImportError:
    import mock

from .. import extedit, helpers, object_types, zodbsync
from ..main import Runner
from . import environment as env


class DummyResponse:
    """
    For mocking the request in extedit test
    """

    def __init__(self, app):
        self.headers = {}
        self.app = app

    def __enter__(self):
        self.orig_request = self.app.REQUEST
        self.app.REQUEST = helpers.Namespace(
            _auth="dummy",
            RESPONSE=self,
        )
        return self

    def __exit__(self, *args):
        self.app.REQUEST = self.orig_request

    def setHeader(self, key, value):
        self.headers[key] = value


class TestSync:
    """
    All tests defined in this class automatically use the environment fixture
    (ZEO, repo etc.)
    """

    @pytest.fixture(scope="class", autouse=True)
    @classmethod
    def environment(cls, request):
        """
        Fixture that is automatically used by all tests. Initializes
        environment and injects the elements of it into the class.
        """
        myenv = dict(
            zeo=env.ZeoInstance(),
            repo=env.Repository(),
            jslib=env.JSLib(),
        )
        myenv["zopeconfig"] = env.ZopeConfig(zeosock=myenv["zeo"].sockpath())
        myenv["config"] = env.ZODBSyncConfig(env=myenv)

        # inject items into class so methods can use them
        for key, value in myenv.items():
            setattr(cls, key, value)

        # Initially record everything and commit it
        me = cls()
        me.run("record", "/")
        me.gitrun("add", ".")
        me.gitrun("commit", "-m", "init")
        cls.initial_commit = me.get_head_id()

        # at this point, the test is called
        yield

        # clean up items
        for item in myenv.values():
            item.cleanup()

    @pytest.fixture(scope="function", autouse=True)
    def envreset(self, request):
        """
        Reset the environment after each test.
        """
        self.run("record", "/")
        # Call test
        yield
        if getattr(self, "runner", None):
            self.runner.sync.tm.abort()
        cmds = [
            "reset --hard",
            "clean -dfx",
            "checkout autotest",
            "reset --hard {}".format(self.initial_commit),
        ]
        for cmd in cmds:
            self.gitrun(*cmd.split())
        output = self.gitoutput("show-ref", "--heads")
        for line in output.strip().split("\n"):
            commit, refname = line.split()
            refname = refname[len("refs/heads/") :]
            if refname != "autotest":
                self.gitrun("branch", "-D", refname)

        self.run("playback", "--skip-errors", "/")

    @contextmanager
    def newconn(self):
        "Add secondary connection"
        tm = transaction.TransactionManager()
        db = ZEO.DB(self.zeo.sockpath())
        conn = db.open(tm)
        app = conn.root.Application
        with tm:
            # Log in, manage_renameObject checks permissions
            userfolder = app.acl_users
            user = userfolder.getUser("perfact").__of__(userfolder)
            newSecurityManager(None, user)

        yield helpers.Namespace({"tm": tm, "app": app})
        tm.abort()
        conn.close()
        db.close()

    @pytest.fixture(scope="function")
    def conn(self, request):
        """
        Fixture that provides a secondary connection to the same ZEO
        """
        with self.newconn() as conn:
            yield conn

    @classmethod
    def mkrunner(cls, *cmd):
        """
        Create or update runner for given zodbsync command.
        Runner and app are stored on the class so a single ZODBSync connection
        is shared across all test instances, avoiding per-test FD accumulation.
        """
        if not hasattr(cls, "runner"):
            cls.runner = Runner()
        result = cls.runner.parse("--config", cls.config.path, *cmd)
        cls.app = cls.runner.sync.app if cls.runner.sync else None
        return result

    def run(self, *cmd):
        "Create runner and run"
        self.mkrunner(*cmd).run()

    def gitrun(self, *cmd):
        """
        Run git command.
        """
        subprocess.check_call(["git", "-C", self.repo.path] + list(cmd))

    def gitoutput(self, *cmd):
        """
        Run git command, returning output.
        """
        return subprocess.check_output(
            ["git", "-C", self.repo.path] + list(cmd),
            universal_newlines=True,
        )

    def upload_checks(self, replace_periods=True, ignore=True):
        """A bunch of asserts to call after an upload test has been performed"""
        assert "lib" in self.app.objectIds()
        assert "js" in self.app.lib.objectIds()
        assert "plugins" in self.app.lib.js.objectIds()
        something_js = "something_js" if replace_periods else "something.js"
        assert something_js in self.app.lib.js.plugins.objectIds()
        content = "alert(1);\n"
        data = helpers.to_string(getattr(self.app.lib.js.plugins, something_js).data)
        assert content == data

        assert "css" in self.app.lib.objectIds()
        assert "skins" in self.app.lib.css.objectIds()
        dark_css = "dark_css" if replace_periods else "dark.css"
        assert dark_css in self.app.lib.css.skins.objectIds()
        content = "body { background-color: black; }\n"
        data = helpers.to_string(getattr(self.app.lib.css.skins, dark_css).data)
        assert content == data

        # dont forget ignored files!
        if ignore:
            assert "ignoreme" not in self.app.lib

    def test_record(self):
        """Recorder tests"""
        # Record everything and make sure acl_users exists
        assert os.path.isfile(self.repo.path + "/__root__/acl_users/__meta__")
        # Recording a non-existent object fails
        with pytest.raises(AttributeError):
            self.run("record", "/nonexist")
        # ... unless --skip-errors is given
        self.run("record", "/nonexist", "--skip-errors")
        # Recording with --lasttxn will create the file
        self.run("record", "--lasttxn")
        assert os.path.isfile(os.path.join(self.repo.path, "__last_txn__"))
        # Making a change with a comment indicating the path will make lasttxn
        # pick it up
        tm = self.runner.sync.start_transaction(note="/testpt")
        self.app.manage_addProduct["PageTemplates"].manage_addPageTemplate(
            id="testpt", text="test1"
        )
        tm.commit()
        self.run("record", "--lasttxn")
        assert os.path.isdir(os.path.join(self.repo.path, "__root__/testpt"))

    def test_record_commit(self):
        """Record with --commit (but no mail and no autoreset)"""
        add = self.app.manage_addProduct["PageTemplates"].manage_addPageTemplate
        with self.runner.sync.tm:
            add(id="test", text="test")
        self.run("record", "/", "--commit")
        # Additional run that does no commit since  nothing changed
        self.run("record", "/", "--commit")
        assert os.path.isdir(os.path.join(self.repo.path, "__root__/test"))
        commits = self.gitoutput("log", "--format=%s")
        assert commits == "Generic commit message.\ninit\n"

    def test_record_autoreset(self):
        """Record with --commit --autoreset."""
        add = self.app.manage_addProduct["PageTemplates"].manage_addPageTemplate
        with self.runner.sync.tm:
            add(id="test", text="test")
        self.run("record", "/", "--commit", "--autoreset")
        assert not os.path.isdir(os.path.join(self.repo.path, "__root__/test"))
        commits = self.gitoutput("log", "--format=%s")
        assert commits == "init\n"
        assert "test" not in self.app.objectIds()

    def test_record_unsupported(self):
        """Check that reading /error_log yields an unsupported marker or an
        error."""
        obj = self.runner.sync.app.error_log
        assert "unsupported" in zodbsync.mod_read(obj)
        with pytest.raises(AssertionError):
            zodbsync.mod_read(obj, onerrorstop=True)

    def test_omit_callable_title(self):
        """It omits title attributes which are callable."""
        app = self.app
        obj = app.manage_addProduct["PageTemplates"].manage_addPageTemplate(
            id="test_pt", title="Not-visible", text="test text"
        )

        def patch_title():
            """Callable to test callable titles."""
            return "Show-me"

        # Normal case
        result = zodbsync.mod_read(obj)
        assert "Not-visible" in result["title"]

        # with callable title
        with mock.patch.object(obj, "title", patch_title):
            result = zodbsync.mod_read(obj)
            assert "title" not in result

    def test_playback(self):
        """
        Record everything, change /index_html, play it back and check if the
        contents are correct.
        """
        path = self.repo.path + "/__root__/index_html/__source-utf8__.html"
        content = "<html></html>"
        with open(path, "w") as f:
            f.write(content)
        self.run("playback", "/index_html")
        assert self.app.index_html() == content

    def add_folder(self, name, msg=None, parent=""):
        """
        Add a folder to the root directory and commit it if msg is given
        """
        folder = os.path.join(self.repo.path, "__root__", parent, name)
        os.mkdir(folder)
        with open(folder + "/__meta__", "w") as f:
            f.write(zodbsync.mod_format({"title": "", "type": "Folder"}))
        if msg is not None:
            self.gitrun("add", ".")
            self.gitrun("commit", "-m", msg)

    def get_head_id(self):
        """Return commit ID of current HEAD."""
        return self.gitoutput("show-ref", "--head", "--hash", "HEAD").strip()

    def prepare_pick(self, name="TestFolder", msg="Second commit"):
        """
        Prepare a commit containing a new folder that can be picked onto the
        initialized repository. Returns the commit ID.
        """
        # Add a folder, commit it
        self.add_folder(name, msg)
        commit = self.get_head_id()

        # Reset the commit
        self.gitrun("reset", "--hard", "HEAD~")

        return commit

    def test_pick(self):
        """
        Pick a prepared commit and check that the folder exists.
        """
        commit = self.prepare_pick()
        self.run("pick", commit)

        assert "TestFolder" in self.app.objectIds()

    def test_pick_dryrun(self):
        """
        Pick a prepared commit in dry-run mode and check that the folder does
        not exist.
        """
        commit = self.prepare_pick()
        self.run("pick", commit, "--dry-run")

        assert "TestFolder" not in self.app.objectIds()

    def test_pick_grep(self):
        """
        Prepare three commits where the first and third share a common pattern
        in the commit message, then pick only those.
        """
        msgs = [
            "T123: first commit",
            "T456: second commit",
            "T123: third commit",
        ]
        for nr, msg in enumerate(msgs):
            self.add_folder("Test" + str(nr), msg)
        commit = self.get_head_id()
        self.gitrun("reset", "--hard", "HEAD~3")
        self.run("pick", "--grep=T123", commit)

        ids = self.app.objectIds()
        assert "Test0" in ids
        assert "Test1" not in ids
        assert "Test2" in ids

    def test_pick_range(self):
        """
        Prepare three commits and pick them as a range
        """
        for i in range(3):
            self.add_folder("Test" + str(i), "Commit " + str(i))
        commit = self.get_head_id()
        self.gitrun("reset", "--hard", "HEAD~3")
        self.run("pick", "HEAD.." + commit)
        ids = self.app.objectIds()
        for i in range(3):
            assert "Test" + str(i) in ids

    def test_pick_fail(self):
        """
        Pick a commit twice, making sure it fails and is rolled back.
        Also pick one applyable and one unknown commit.
        """
        commit = self.prepare_pick()
        for second in [commit, "unknown"]:
            with pytest.raises(subprocess.CalledProcessError):
                self.run("pick", commit, second)
            assert "TestFolder" not in self.app.objectIds()
            assert not os.path.isdir(self.repo.path + "/__root__/TestFolder")

    def test_upload_relpath(self):
        """
        Upload JS library from test environment and check for it in Data.fs
        Provide Data.fs path only
        """

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join("__root__", "lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )

        self.upload_checks()

        # we may even omit __root__ in path!
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join("lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )

        self.upload_checks()

        # add another test case showing dot notation also works
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join(".", "lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )

        self.upload_checks()

    def test_upload_options(self):
        """
        Test upload with different options settings.
        """
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join("__root__", "lib")

        self.run(
            "upload",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )
        self.upload_checks(replace_periods=False)

        self.run("upload", "--replace-periods", target_jslib_path, target_repo_path)
        self.upload_checks(ignore=False)

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "  ,,css,js, ",
            target_jslib_path,
            target_repo_path,
        )
        self.upload_checks(ignore=True)

        self.run("upload", target_jslib_path, target_repo_path)
        self.upload_checks(replace_periods=False, ignore=False)

    def test_upload_relpath_fromrepo(self):
        """
        change working directory to repository before upload to simulate
        calling upload from repo leveraging bash path completion
        """
        cur_path = os.getcwd()
        os.chdir(self.repo.path)

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join(".", "__root__", "lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )

        self.upload_checks()

        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join("__root__", "lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
        )

        self.upload_checks()

        os.chdir(cur_path)

    def test_upload_dryrun(self):
        """
        Upload files in dryrun mode, make sure folder is not found in Data.fs
        """
        target_jslib_path = self.jslib.path
        target_repo_path = os.path.join("__root__", "lib")

        self.run(
            "upload",
            "--replace-periods",
            "--valid-extensions",
            "css,js",
            target_jslib_path,
            target_repo_path,
            "--dry-run",
        )

        assert "lib" not in self.app.objectIds()

    def test_emptying_userdefined_roles(self):
        """
        Check fix for #22: if a Folder defines local roles, playback must be
        able to remove them.
        """
        with self.runner.sync.tm:
            self.app._addRole("TestRole")
        self.run("record", "/")
        fname = self.repo.path + "/__root__/__meta__"
        with open(fname, "r") as f:
            lines = f.readlines()
        with open(fname, "w") as f:
            f.writelines([line for line in lines if "TestRole" not in line])
        self.runner.sync.playback_paths(paths=["/"], recurse=False)
        assert self.app.userdefined_roles() == ()

    def test_userdefined_roles_playback(self):
        """
        Test fix #57: Make sure that playback of an object with local roles
        works correctly. Set a local role, record, read out the recording, play
        back, check that it is set correctly, record again and check that the
        recording matches the first one.
        """
        with self.runner.sync.tm:
            self.app._addRole("TestRole")
            self.app.manage_setLocalRoles("perfact", ("TestRole",))
        self.run("record", "/")

        fname = self.repo.path + "/__root__/__meta__"
        with open(fname, "r") as f:
            recording = f.read()
        self.runner.sync.playback_paths(paths=["/"], recurse=False)
        assert self.app.get_local_roles() == (("perfact", ("TestRole",)),)
        self.runner.sync.record("/", recurse=False)
        with open(fname, "r") as f:
            assert recording == f.read()

    def test_addprop(self):
        "Add a property to the root object"
        fname = self.repo.path + "/__root__/__meta__"
        with open(fname, "r") as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        prop = {
            "id": "testprop",
            "type": "string",
            "value": "test",
        }
        data["props"] = [list(prop.items())]
        with open(fname, "w") as f:
            f.write(zodbsync.mod_format(data))
        self.run("playback", "/")
        assert self.app.getProperty("testprop") == "test"

    def test_addtokenprop(self):
        "Validate tokens are correctly written"
        fname = self.repo.path + "/__root__/__meta__"
        with open(fname, "r") as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        prop = {
            "id": "testprop",
            "type": "tokens",
            "value": ("123", "518"),
        }
        data["props"] = [list(prop.items())]
        with open(fname, "w") as f:
            f.write(zodbsync.mod_format(data))
        self.run("playback", "/")
        assert self.app.getProperty("testprop") == ("123", "518")

    def test_changeprop(self):
        "Change first the value and then the type of a property"
        with self.runner.sync.tm:
            self.app.manage_addProperty("testprop", "test", "string")
        fname = self.repo.path + "/__root__/__meta__"
        self.run("record", "/")
        with open(fname, "r") as f:
            content = f.read()
        data = dict(helpers.literal_eval(content))
        for ptype, pval in [("string", "changed"), ("int", 1)]:
            prop = {
                "id": "testprop",
                "type": ptype,
                "value": pval,
            }
            data["props"] = [list(prop.items())]
            with open(fname, "w") as f:
                f.write(zodbsync.mod_format(data))
            self.run("playback", "/")
            assert self.app.getProperty("testprop") == pval
            assert self.app.getPropertyType("testprop") == ptype

    def test_cacheable(self):
        "Add a RamCacheManager and use it for index_html"
        with self.runner.sync.tm:
            self.app.manage_addProduct[
                "StandardCacheManagers"
            ].manage_addRAMCacheManager(id="http_cache")
            self.app.index_html.ZCacheable_setManagerId("http_cache")
        self.run("record", "/")
        fname = self.repo.path + "/__root__/index_html/__meta__"
        assert "http_cache" in open(fname).read()
        self.run("playback", "/")
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
        fname = self.repo.path + "/__root__/__meta__"
        watcher = self.mkrunner("watch")
        watcher.setup()
        conn.tm.begin()
        conn.app._addRole("TestRole")
        watcher.step()
        assert "TestRole" not in open(fname).read()
        conn.tm.commit()
        self.watcher_step_until(watcher, lambda: "TestRole" in open(fname).read())

    def test_watch_move(self, conn):
        """
        Create a Page Template, record it using the watcher, rename it and make
        sure the watcher notices. Then add a second one and do a
        three-way-rename in one transaction, making sure the watcher keeps
        track.
        """
        watcher = self.mkrunner("watch")
        watcher.setup()
        root = self.repo.path + "/__root__/"
        src = "/__source-utf8__.html"
        app = conn.app

        add = app.manage_addProduct["PageTemplates"].manage_addPageTemplate
        rename = app.manage_renameObject

        with conn.tm:
            add(id="test1", text="test1")
        self.watcher_step_until(watcher, lambda: os.path.isdir(root + "test1"))

        with conn.tm:
            rename("test1", "test2")
        self.watcher_step_until(watcher, lambda: os.path.isdir(root + "test2"))
        assert not os.path.isdir(root + "test1")

        with conn.tm:
            add(id="test1", text="test2")
        self.watcher_step_until(watcher, lambda: os.path.isdir(root + "test1"))

        assert os.path.isdir(root + "test1")
        assert open(root + "test1" + src).read() == "test2"
        assert open(root + "test2" + src).read() == "test1"

        with conn.tm:
            rename("test1", "tmp")
            rename("test2", "test1")
            rename("tmp", "test2")
        self.watcher_step_until(
            watcher,
            lambda: open(root + "test1" + src).read() == "test1",
        )
        assert open(root + "test1" + src).read() == "test1"
        assert open(root + "test2" + src).read() == "test2"

    def test_watch_dump_setup(self):
        """
        Check output that a spawned initialization subprocess would generate.
        """
        watcher = self.mkrunner("watch")
        watcher.setup()
        stream = io.BytesIO()
        watcher.dump_setup_data(stream=stream)
        data = pickle.loads(stream.getvalue())
        assert set(data.keys()) == {"tree", "txn", "add_oids"}
        tofind = ["/", "/acl_users/", "/index_html/"]
        for obj in data["tree"].values():
            if obj["path"] in tofind:
                tofind.remove(obj["path"])
        assert tofind == []

    def test_ff(self):
        """
        Change the title on a second branch,
        perform a fast-forward merge to it,
        and verify that the change is correctly applied.
        """
        self.gitrun("checkout", "-b", "second")
        path = self.repo.path + "/__root__/index_html/__meta__"
        with open(path) as f:
            lines = f.readlines()
        lines = [
            line if "('title', " not in line else "    ('title', 'test-ff'),\n"
            for line in lines
        ]
        with open(path, "w") as f:
            f.writelines(lines)
        self.gitrun("commit", "-a", "-m", "Change title via ff")

        self.gitrun("checkout", "autotest")
        self.run("ff", "second")
        assert self.app.index_html.title == "test-ff"

    def test_reset(self):
        """
        Change the title of index_html in a second branch, reset to it and
        check that it is played back correctly.
        """
        self.gitrun("checkout", "-b", "second")
        path = self.repo.path + "/__root__/index_html/__meta__"
        with open(path) as f:
            lines = f.readlines()
        lines = [
            line if "('title', " not in line else "    ('title', 'test'),\n"
            for line in lines
        ]
        with open(path, "w") as f:
            f.writelines(lines)
        self.gitrun("commit", "-a", "-m", "Change title")
        self.gitrun("checkout", "autotest")
        self.run("reset", "second")
        assert self.app.index_html.title == "test"

    def test_revert(self):
        """
        Do the same as in test_reset, but afterwards revert it.
        """
        self.test_reset()
        self.run("exec", "git revert HEAD")
        title = self.app.index_html.title
        assert title != "test"

    def test_checkout(self):
        """
        Switch to another branch
        """
        self.run("checkout", "-b", "other")
        # This switches back to autotest, but with a change
        self.test_reset()
        self.run("checkout", "other")
        assert self.app.index_html.title != "test"
        self.run("checkout", "autotest")
        assert self.app.index_html.title == "test"

    def test_exec_checkout(self):
        """
        Prepare two branches and switch between them.
        """
        self.gitrun("branch", "other")
        self.test_reset()
        self.run("exec", "git checkout other")
        title = self.app.index_html.title
        assert title != "test"

    def test_withlock(self):
        "Running with-lock and, inside that, --no-lock, works"
        self.run(
            "with-lock",
            "zodbsync --config {} --no-lock record /".format(self.config.path),
        )

    def test_extedit(self, encoding=None):
        """
        Update /index_html using the external editor launcher
        """
        header_lines = [
            "url: index_html",
            "path: //index_html",
            "auth: dummy",
            "meta-type: Page Template",
            "content-type: text/html",
        ]
        new_source = "test"
        with DummyResponse(self.app) as resp:
            # Read control file
            content = extedit.launch(
                self.app,
                self.app.index_html,
                "/index_html",
            )
            headers, orig_source = content.split("\n\n", 1)
            assert headers == "\n".join(header_lines)
            assert resp.headers["Content-Type"] == ("application/x-perfact-zopeedit")

            # Update to new content
            if encoding:
                orig_source, new_source = [
                    helpers.to_string(base64.b64encode(helpers.to_bytes(item)))
                    for item in [orig_source, new_source]
                ]
            res = extedit.launch(
                self.app,
                self.app.index_html,
                "/index_html",
                source=new_source,
                orig_source=orig_source,
                encoding=encoding,
            )
            assert "success" in res
            assert resp.headers["Content-Type"] == "application/json"
            assert self.app.index_html._text == "test"

            # Try the update again, which must fail because the orig_source no
            # longer matches
            res = extedit.launch(
                self.app,
                self.app.index_html,
                "/index_html",
                source=new_source,
                orig_source=orig_source,
                encoding=encoding,
            )
            assert "error" in json.loads(res)

            # Check for error on invalid path
            res = extedit.launch(
                self.app,
                self.app,
                "/nonexist",
                source="",
                orig_source="",
            )
            assert res == '{"error": "/nonexist not found"}'

    def test_extedit_base64(self):
        self.test_extedit(encoding="base64")

    def test_extedit_binary(self):
        "Test with binary file that is not valid UTF-8"
        self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with DummyResponse(self.app):
            extedit.launch(
                self.app,
                self.app,
                "/blob",
                source=helpers.to_string(base64.b64encode(b"\xff")),
                orig_source="",
                encoding="base64",
            )
            assert self.app.blob.data == b"\xff"

            res = extedit.launch(
                self.app,
                self.app.blob,
                "/blob",
            )
            assert res.endswith("\n\n/w==")

    def meta_file_path(self, *folders):
        """
        takes n folders in order as arguments and returns path to meta file
        """
        path = self.repo.path + "/__root__/"
        for folder in folders:
            path = path + folder + "/"
        path = path + "__meta__"
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
        assert "s_folder_1" in self.app.folder_1.objectIds()

        # record structure and check that the objects are recorded
        self.run("record", "/")
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        # set new title
        path = self.meta_file_path(folder_1, s_folder_1)
        new_title = "new_title"
        content = "[('title', '" + new_title + "'),('type', 'Folder'),]"
        with open(path, "w") as f:
            f.write(content)

        # create metadata for new folder
        new_folder = "new_folder"
        path = (
            self.repo.path
            + "/__root__/"
            + folder_1
            + "/"
            + s_folder_1
            + "/"
            + new_folder
        )
        os.mkdir(path)
        with open(path + "/__meta__", "w") as f:
            f.write(
                """[
                ('id', '{}'),
                ('title', ''),
                ('type', 'Folder'),
            ]""".format(new_folder)
            )

        # playback changes and check if they're existent
        self.run("playback", "/")
        assert new_title == self.app.folder_1.s_folder_1.title
        assert new_folder in self.app.folder_1.s_folder_1.objectIds()

    def test_watch_structure_changes_and_playback_local_changes(self, conn):
        """
        create structure while 'watch' command is running,
        add local changes, then play those changes back and check,
        if those changes played back correctly
        """

        # start watch daemon
        watcher = self.mkrunner("watch")
        watcher.setup()
        app = conn.app
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"

        # create folder and wait until watch notices change
        with conn.tm:
            app.manage_addFolder(id=folder_1)
        self.watcher_step_until(
            watcher, lambda: os.path.isdir(self.repo.path + "/__root__/" + folder_1)
        )

        # create subfolder and wait until watch notices change
        with conn.tm:
            app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        path = self.repo.path + "/__root__/" + folder_1 + "/" + s_folder_1
        self.watcher_step_until(watcher, lambda: os.path.isdir(path))

        # change title
        new_title = "new_title"
        path = self.meta_file_path(folder_1, s_folder_1)
        content = "[('title', '" + new_title + "'),('type', 'Folder'),]"
        with open(path, "w") as f:
            f.write(content)

        # playback changes and check if those are existent in zodb
        self.run("playback", "/")
        assert new_title == self.app.folder_1.s_folder_1.title

        # wait for watch to notices played back changes
        with open(path) as f:
            meta = f.read()
        self.watcher_step_until(
            watcher, lambda: "('title', '" + new_title + "')" in meta
        )

    def test_watch_structure_changes_and_playback_deleted_folder(self, conn):
        """
        Create structure while 'watch' command is running, remove a folder,
        then play those changes back and check that the watcher handles this
        without crashing.
        """

        # start watch daemon
        watcher = self.mkrunner("watch")
        watcher.setup()
        app = conn.app
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"

        # create folder and wait until watch notices change
        with conn.tm:
            app.manage_addFolder(id=folder_1)
        self.watcher_step_until(
            watcher, lambda: os.path.isdir(self.repo.path + "/__root__/" + folder_1)
        )

        # create subfolder and wait until watch notices change
        with conn.tm:
            app.folder_1.manage_addFolder(id=s_folder_1, title=s_folder_1)
        path = self.repo.path + "/__root__/" + folder_1 + "/" + s_folder_1
        self.watcher_step_until(watcher, lambda: os.path.isdir(path))

        # remove folder s_folder_1
        shutil.rmtree(path)

        # playback changes and check if those are existent in zodb
        self.run("playback", "/")

        # wait for watch to notices played back changes
        self.watcher_step_until(watcher, lambda: not os.path.isdir(path))

    def test_commit_on_branch_and_exec_merge(self):
        """
        change to a git feature branch and create a
        structure there, commit it and change back to the autotest branch
        on autotest branch check if changes from feature arent existent,
        then merge feature branch and check if changes have been applied
        correctly
        """

        # change to feature branch and commit created folder/ subfolder
        branch = "feature"
        folder_1 = "folder_1"
        s_folder_1 = "s_folder_1"
        self.run("exec", "git checkout -b {}".format(branch))
        self.app.manage_addFolder(id=folder_1)
        self.app.folder_1.manage_addFolder(id=s_folder_1)
        assert s_folder_1 in self.app.folder_1.objectIds()
        self.run("record", "/")
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        self.gitrun("add", "-A")
        self.gitrun("commit", "-m", "test case 3")

        # checkout to autotest and check that changes are not yet existent
        self.run("exec", "git checkout autotest")
        assert not os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        assert folder_1 not in self.app.objectIds()

        # merge feature branch and check that changes are applied
        self.run("exec", "git merge {}".format(branch))
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
        self.run("record", "/")

        # break metadata
        path = self.repo.path + "/__root__/" + folder_1 + "/__meta__"
        content = "[('gandalf', 'ThisIsAWrongKey'),]"
        with open(path, "w") as f:
            f.write(content)

        # test that playback fails
        with pytest.raises(KeyError):
            self.run("playback", "/")

    def test_failing_exec_commands(self):
        """
        call exec commands with wrong commits and
        check if exceptions are thrown correctly
        """
        with pytest.raises(subprocess.CalledProcessError):
            self.run("exec", "revert ThisIsDefinitelyNoCommit")

        with pytest.raises(subprocess.CalledProcessError):
            self.run("exec", "reset ThisIsDefinitelyNoCommit")

        with pytest.raises(subprocess.CalledProcessError):
            self.run("exec", "cherry-pick ThisIsDefinitelyNoCommit")

    def test_create_multiple_commits_on_branch_and_pick_single_on_autotest(self):
        """
        create a feature branch on which
        two changes will be commited to one commit each
        change back to the autotest branch and use pick
        to get the changes of that last commit
        make sure only the last changes are present
        """
        branch = "feature"
        self.gitrun("checkout", "-b", branch)

        # make first changes and commit those
        folder_1 = "folder_1"
        self.app.manage_addFolder(id=folder_1)
        assert folder_1 in self.app.objectIds()
        self.run("record", "/")
        assert os.path.isfile(self.meta_file_path(folder_1))
        self.gitrun("add", "-A")
        self.gitrun("commit", "-m", "pick_commit_1")

        # make second changes and commit those
        folder_2 = "sf_2_tc6"
        self.app.manage_addFolder(id=folder_2)
        assert folder_2 in self.app.objectIds()
        self.run("record", "/")
        assert os.path.isfile(self.meta_file_path(folder_2))
        self.gitrun("add", "-A")
        self.gitrun("commit", "-m", "pick_commit_2")

        commit = self.get_head_id()

        # checkout autotest and check both changes aren't existent
        self.run("exec", "git checkout autotest")
        assert not os.path.isfile(self.meta_file_path(folder_1))
        assert folder_1 not in self.app.objectIds()
        assert not os.path.isfile(self.meta_file_path(folder_2))
        assert folder_2 not in self.app.objectIds()

        # pick 2nd commit and check that
        # first arent' but second changes are applied
        self.run("pick", commit)
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
        self.run("record", "/")
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))

        self.gitrun("add", "-A")
        self.gitrun("commit", "-m", "reset_commit_1")

        # create second changes and commit those
        path = self.repo.path + "/__root__/" + folder_1 + "/" + s_folder_1 + "/__meta__"
        new_title = "new_title"
        content = "[('title', '" + new_title + "'),('type', 'Folder'),]"
        with open(path, "w") as f:
            f.write(content)
        new_folder = "new_folder"
        path = (
            self.repo.path
            + "/__root__/"
            + folder_1
            + "/"
            + s_folder_1
            + "/"
            + new_folder
        )
        os.mkdir(path)
        with open(path + "/__meta__", "w") as f:
            f.write(
                """[
                ('id', '{}'),
                ('title', ''),
                ('type', 'Folder'),
            ]""".format(new_folder)
            )
        self.run("playback", "/")

        self.gitrun("add", "-A")
        self.gitrun("commit", "-m", "reset_commit_2")

        # check that changes are existent in zodb
        assert new_title == self.app.folder_1.s_folder_1.title
        assert new_folder in self.app.folder_1.s_folder_1.objectIds()

        # reset HEAD by one commit and check that second changes are
        # not existent anymore but first changes still are
        self.run("reset", "HEAD~1")
        assert folder_1 in self.app.objectIds()
        assert s_folder_1 in self.app.folder_1.objectIds()
        assert os.path.isfile(self.meta_file_path(folder_1, s_folder_1))
        assert new_title != self.app.folder_1.s_folder_1.title
        assert new_folder not in self.app.folder_1.s_folder_1.objectIds()

        # reset HEAD by one commit and check that first changes are
        # not existent anymore
        self.run("reset", "HEAD~1")
        assert folder_1 not in self.app.objectIds()
        assert not os.path.isfile(self.meta_file_path(folder_1))

    @pytest.mark.parametrize("meta_type", object_types.object_handlers)
    def test_objecttypes(self, meta_type):
        """
        Generic test that is executed for each coded object type. This creates
        an object and writes a modification to it, without actually checking
        for anything. Some are known to fail, for example because they need
        products that are not published on pypi or because they need external
        ressources like non-free libraries for external data connections.
        """
        if meta_type in [
            "DTML TeX",
            "ZForce",
            "External Method",
            "Z cxOracle Database Connection",
            "Z sap Database Connection",
        ]:
            pytest.skip("Skipping objects that require elaborate dependencies")

        if "Test" not in self.app.objectIds():
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Test")
        if meta_type in ["User Folder", "Simple User Folder"]:
            objid = "acl_users"
        else:
            objid = "testobj"
        parent = self.app.Test
        handler = object_types.object_handlers[meta_type]
        # data that is required by some objects and ignored by others
        add_data = {
            "title": "test",
            "content_type": "text/plain",
            "connection_id": "dbconn",
            "connection_string": "",
            "autocommit": False,
            "maxrows": 100,
            "args": "",
            "source": "",
            "smtp_host": "localhost",
            "smtp_port": "25",
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
            self.app.manage_addProduct["OFSP"].manage_addOrderedFolder(id="Test")
            self.app.Test.manage_addProduct["OFSP"].manage_addFolder(id="exist")
        assert self.app.Test.objectIds() == ["exist"]
        self.run("record", "/")

        folder = self.repo.path + "/__root__/Test/"

        new_folder = folder + "new"
        os.mkdir(new_folder)

        with open(os.path.join(new_folder, "__meta__"), "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "title": "",
                        "type": "Folder",
                    }
                )
            )

        with open(folder + "__meta__", "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "contents": ["new", "exist"],
                        "title": "",
                        "type": "Folder (Ordered)",
                    }
                )
            )
        self.run("playback", "--no-recurse", "/Test", "/Test/new")
        assert self.app.Test.objectIds() == ["new", "exist"]

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
            parent.manage_addProduct["OFSP"].manage_addFolder(id=fid)

        with self.runner.sync.tm:
            add(self.app, "Test")
            for child in ["A", "B", "C"]:
                add(self.app.Test, child)
        self.run("record", "/")
        meta = "{}/__root__/Test/__meta__".format(self.repo.path)

        with open(meta, "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "contents": ["B", "A"],
                        "title": "change",
                        "type": "Folder (Ordered)",
                    }
                )
            )
        orig_oid = self.app.Test.A._p_oid
        self.run("playback", "/Test", "--override")
        assert self.app.Test.meta_type == "Folder (Ordered)"
        ids = self.app.Test.objectIds()
        assert sorted(ids) == ["A", "B", "C"]
        assert ids.index("B") < ids.index("A")
        assert self.app.Test.A._p_oid == orig_oid

        with open(meta, "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "title": "change again",
                        "type": "Folder",
                    }
                )
            )
        self.run("playback", "/Test", "--override")
        assert self.app.Test.meta_type == "Folder"
        assert sorted(self.app.Test.objectIds()) == ["A", "B", "C"]
        assert self.app.Test.A._p_oid == orig_oid

        with self.runner.sync.tm:
            self.app.Test.manage_delObjects(ids=["A", "B", "C"])
        self.run("record", "/")
        with open(meta, "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "title": "change",
                        "type": "Folder (Ordered)",
                    }
                )
            )
        self.run("playback", "/Test", "--override")
        assert self.app.Test.meta_type == "Folder (Ordered)"

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
            self.app.manage_delObjects("acl_users")
            self.runner.sync.create_manager_user()
        self.run("playback", "/")
        assert self.app.acl_users.meta_type == "User Folder"

    def test_no_unnecessary_writes(self):
        """
        Check that recording or playing back an unchanged object does not
        actually update it.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="test")

        folder = self.app.test

        self.run("record", "/test")
        mtime1 = folder._p_mtime
        self.run("playback", "/test")
        mtime2 = folder._p_mtime
        assert mtime1 == mtime2

        path = self.repo.path + "/__root__/test/__meta__"
        fsmtime1 = os.stat(path).st_mtime
        self.run("record", "/test")
        fsmtime2 = os.stat(path).st_mtime
        assert fsmtime1 == fsmtime2

    def test_no_meta_file(self):
        """
        Check that a missing meta file regards the object as deleted.
        """

        broken_obj = os.path.join(self.repo.path, "__root__", "foo")
        os.mkdir(broken_obj)

        self.run("playback", "/foo")
        assert "foo" not in self.app.objectIds()

        self.add_folder("Test")
        self.run("playback", "/Test")
        os.remove(os.path.join(self.repo.path, "__root__/Test/__meta__"))
        self.run("playback", "/Test")
        assert "Test" not in self.app.objectIds()

    def test_force_default_owner(self):
        """
        Check if the default owner can be forced via config
        """

        self.runner.sync.force_default_owner = True

        # first test: owner from meta file pushed to app
        folder = os.path.join(self.repo.path, "__root__", "newfolder")
        os.mkdir(folder)

        with open(os.path.join(folder, "__meta__"), "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "title": "",
                        "type": "Folder",
                        "owner": (["acl_users"], "Somebody"),
                    }
                )
            )

        self.run("playback", "/newfolder")

        expected_owner = (["acl_users"], self.runner.sync.default_owner)

        assert self.app.newfolder._owner == expected_owner

        # second test: owner from zope read to meta file
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="another")

        self.app.another._owner = (["acl_users"], "Somebody")

        self.run("record", "/")

        meta = self.runner.sync.fs_parse(
            os.path.join(self.repo.path, "__root__/another")
        )

        assert "owner" not in meta

    def test_force_default_owner_negative(self):
        """
        Negative test for force_default_owner setting: Make sure we see
        to old behaviour without this setting being set
        """

        self.runner.sync.force_default_owner = False

        # first test: owner from meta file pushed to app
        folder = os.path.join(self.repo.path, "__root__", "newfolder")
        os.mkdir(folder)

        with open(os.path.join(folder, "__meta__"), "w") as f:
            f.write(
                zodbsync.mod_format(
                    {
                        "title": "",
                        "type": "Folder",
                        "owner": (["acl_users"], "Somebody"),
                    }
                )
            )

        self.run("playback", "/newfolder")
        assert self.app.newfolder._owner == (["acl_users"], "Somebody")

        # second test: owner from zope read to meta file
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="another")

        self.app.another._owner = (["acl_users"], "Somebody")

        self.run("record", "/")

        meta = self.runner.sync.fs_parse(
            os.path.join(self.repo.path, "__root__/another")
        )

        assert meta["owner"] == (["acl_users"], "Somebody")

    def test_reformat(self):
        """
        Make a couple of commits with changes to a meta file using the legacy
        format. Then reformat them, checking that no error occurs and that the
        final state uses the new formatting.
        """
        folder = os.path.join(self.repo.path, "__root__/Test")
        os.mkdir(folder)
        fname = os.path.join(folder, "__meta__")

        def commit():
            self.gitrun("add", "__root__/Test/__meta__")
            self.gitrun("commit", "-m", "Test")

        def store(data, strip=False):
            # With strip=False, simulate an older version where there was no
            # newline at the end of meta files
            with open(fname, "w") as f:
                s = helpers.StrRepr()(data, legacy=True)
                if strip:
                    s = s.strip()
                f.write(s)
            commit()

        store(
            {
                "title": "Zope",
                "roles": ["A"],
                "perms": [("View", False, ["Anonymous"])],
            }
        )
        start = self.get_head_id()

        store(
            {
                "title": "Other",
                "roles": ["A", "B"],
                "perms": [("View", True, ["Anonymous", "A"])],
            },
            strip=True,
        )

        # Add a commit that deletes the object while it does not end in a
        # newline. A naive cherry-pick would result in a merge conflict.
        shutil.rmtree(folder)
        commit()
        os.mkdir(folder)

        store(
            {
                "title": "Other",
                "props": [
                    [("id", "columns"), ("type", "tokens"), ("value", ("a", "b", "c"))],
                ],
            }
        )

        self.run("reformat", start)
        with open(fname) as f:
            fmt = f.read()
        assert fmt.strip().split("\n") == [
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
            self.app._setProperty("test", "foo", "string")

        self.run("record", "/")
        self.gitrun("add", ".")
        self.gitrun("commit", "-m", "with property")
        c1 = self.get_head_id()

        with self.runner.sync.tm:
            self.app.manage_delProperties(ids=["test"])
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="test")

        self.run("record", "/")
        self.gitrun("add", ".")
        self.gitrun("commit", "-m", "with child")
        c2 = self.get_head_id()

        self.run("reset", c2)
        self.run("reset", c1)

    @contextmanager
    def appendtoconf(self, text):
        """
        Append some text to the config and restore afterward
        """
        with open(self.config.path) as f:
            orig_config = f.read()
        with open(self.config.path, "a") as f:
            f.write("\n" + text + "\n")
        try:
            yield
        finally:
            with open(self.config.path, "w") as f:
                f.write(orig_config)

    def test_playback_postprocess(self):
        """
        Add configuration option for a postprocessing script and check that
        zodbsync reset executes it.
        """
        fname = "{}/postproc".format(self.zeo.path)
        outfile = "{}.out".format(fname)
        script = "\n".join(
            [
                "#!/bin/bash",
                "cat > {}",
            ]
        ).format(outfile)
        with open(fname, "w") as f:
            f.write(script)
        os.chmod(fname, 0o700)
        with self.appendtoconf('run_after_playback = "{}"'.format(fname)):
            self.test_reset()
            with open(outfile) as f:
                assert json.loads(f.read()) == {"paths": ["/index_html/"]}

    def addscript(self, basename, *lines):
        """
        Add executable file into zeo dir, returning the full filename
        """
        fname = "{}/{}".format(self.zeo.path, basename)
        lines = ("#!/bin/bash",) + lines
        with open(fname, "w") as f:
            f.write("\n".join(lines))
        os.chmod(fname, 0o700)
        return fname

    def test_playback_hook(self):
        """
        Add configuration option for a playback hook script and check that
        only the paths returned are played back
        """
        self.add_folder("NewFolder", "First Folder")
        self.add_folder("NewFolder2", "Second Folder")
        commit = self.get_head_id()
        # Reset the commit
        self.gitrun("reset", "--hard", "HEAD~2")

        playback_cmd_out = "{}/playback_cmd.out".format(self.zeo.path)
        playback_cmd = self.addscript(
            "playback_cmd",
            "cat > {}".format(playback_cmd_out),
        )

        playback_hook = self.addscript(
            "playback_hook",
            "echo '{}'".format(
                json.dumps(
                    [
                        {
                            "paths": ["/NewFolder"],
                            "cmd": playback_cmd,
                        }
                    ]
                )
            ),
        )
        with self.appendtoconf('playback_hook = "{}"'.format(playback_hook)):
            self.run("pick", "HEAD..{}".format(commit))

        assert "NewFolder" in self.app.objectIds()
        assert "NewFolder2" not in self.app.objectIds()
        assert os.path.isfile(playback_cmd_out)

    def test_playback_hook_failed(self):
        """
        Add configuration option for a playback hook script with a
        failing cmd and check that all changes are rolled back
        """
        self.add_folder("NewFolder", "First Folder")
        self.add_folder("NewFolder2", "Second Folder")
        commit = self.get_head_id()
        # Reset the commit
        self.gitrun("reset", "--hard", "HEAD~2")

        playback_cmd = self.addscript(
            "playback_cmd",
            "exit 42",
        )
        playback_hook = self.addscript(
            "playback_hook",
            "echo '{}'".format(
                json.dumps(
                    [
                        {
                            "paths": ["/NewFolder"],
                            "cmd": playback_cmd,
                        },
                        {
                            "paths": ["/NewFolder2"],
                        },
                    ]
                )
            ),
        )
        with self.appendtoconf('playback_hook = "{}"'.format(playback_hook)):
            with pytest.raises(AssertionError):
                self.run("pick", "HEAD..{}".format(commit))

            assert "NewFolder" not in self.app.objectIds()
            assert "NewFolder2" not in self.app.objectIds()

    @contextmanager
    def addlayer(self, seqnum="00"):
        """
        Create a temp directory and add a config that uses this as additional
        code layer.
        """
        name = "{}-{}.py".format(
            seqnum, "".join([random.choice(string.ascii_letters) for _ in range(16)])
        )
        path = "{}/layers/{}".format(self.config.folder, name)
        try:
            with tempfile.TemporaryDirectory() as layer:
                workdir = f"{layer}/workdir"
                os.makedirs(f"{workdir}/__root__")
                subprocess.run(["git", "init"], cwd=workdir)
                subprocess.run(
                    ["git", "config", "user.email", "zodbsync-tester@perfact.de"],
                    cwd=workdir,
                )
                subprocess.run(
                    ["git", "config", "user.name", "ZODBSync tester"], cwd=workdir
                )
                source = f"{layer}/source"
                os.makedirs(f"{source}/__root__")
                with open(path, "w") as f:
                    f.write(f'workdir = "{layer}/workdir"\n')
                    f.write(f'source = "{source}"\n')
                    f.write(f'ident = "{name}"\n')
                # Reload so the new layer is visible without recreating the
                # ZODB connection (which would accumulate file descriptors).
                if hasattr(self, "runner") and self.runner.sync:
                    self.runner.sync.reload_layers()
                yield layer
            # TemporaryDirectory is now gone; remove config before reloading
            # so the stale workdir path is never added back to self.layers.
        finally:
            if os.path.exists(path):
                os.remove(path)
            if hasattr(self, "runner") and self.runner.sync:
                self.runner.sync.reload_layers()

    def test_layer_record_freeze(self):
        """
        Create a folder, copy it into an additional fixed layer, freeze the
        folder and record it. Check that the top layer still has the object
        and a __frozen__ marker.
        """
        self.add_folder("Test", "Test")
        self.run("playback", "/Test")
        with self.addlayer() as layer:
            shutil.copytree(
                "{}/__root__/Test".format(self.repo.path),
                "{}/workdir/__root__/Test".format(layer),
            )
            self.run("freeze", "/")
            self.run("record", "/")
        for fname in ["__meta__", "__frozen__", "Test/__meta__"]:
            assert os.path.exists("{}/__root__/{}".format(self.repo.path, fname))

    def test_layer_record_nofreeze(self):
        """
        Create a folder, copy it into an additional fixed layer and record
        everything. Check that the top layer no longer has the folder.
        """
        self.add_folder("Test", "Test")
        self.run("playback", "/Test")
        with self.addlayer() as layer:
            shutil.copytree(
                "{}/__root__/Test".format(self.repo.path),
                "{}/workdir/__root__/Test".format(layer),
            )
            self.run("record", "/")
        assert not os.path.exists("{}/__root__/Test".format(self.repo.path))

    def test_layer_named_freeze_restricts(self):
        """
        __frozen__ in a named layer restricts layers to that layer and above.
        Object visible in Zope but only in an excluded layer must be recorded
        into the custom layer.
        """
        self.add_folder("Test", "Test")
        self.run("playback", "/Test")
        # layerB (seqnum="00") -> index 2 (lower priority)
        # layerA (seqnum="01") -> index 1 (higher priority)
        with self.addlayer("00") as layerB:
            shutil.copytree(
                f"{self.repo.path}/__root__/Test",
                f"{layerB}/workdir/__root__/Test",
            )
            with self.addlayer("01") as layerA:
                # Remove /Test from custom layer so only layerB has it
                shutil.rmtree(f"{self.repo.path}/__root__/Test")
                # Place __frozen__ in layerA's __root__ (index 1)
                # -> layers[:2] = [custom, layerA]; layerB excluded
                open(f"{layerA}/workdir/__root__/__frozen__", "w").close()
                self.run("record", "/")
        # layerB excluded by freeze -> /Test not seen in consulted layers
        # -> recorded to custom layer
        assert os.path.exists(f"{self.repo.path}/__root__/Test/__meta__")

    def test_layer_named_freeze_sibling_unaffected(self):
        """
        __frozen__ in a named layer at a subtree path does not restrict
        sibling paths outside that subtree.
        """
        self.add_folder("Test", "Test")
        self.add_folder("Other", "Other")
        self.run("playback", "/Test")
        self.run("playback", "/Other")
        with self.addlayer("00") as layerB:
            shutil.copytree(
                f"{self.repo.path}/__root__/Test",
                f"{layerB}/workdir/__root__/Test",
            )
            shutil.copytree(
                f"{self.repo.path}/__root__/Other",
                f"{layerB}/workdir/__root__/Other",
            )
            with self.addlayer("01") as layerA:
                # Remove both from custom layer
                shutil.rmtree(f"{self.repo.path}/__root__/Test")
                shutil.rmtree(f"{self.repo.path}/__root__/Other")
                # Place __frozen__ inside layerA's __root__/Test/ subtree only
                os.makedirs(f"{layerA}/workdir/__root__/Test")
                open(f"{layerA}/workdir/__root__/Test/__frozen__", "w").close()
                self.run("record", "/")
        # /Other is outside the frozen subtree -> layerB still serves it
        # -> custom layer must NOT have /Other
        assert not os.path.exists(f"{self.repo.path}/__root__/Other")

    def test_layer_record_compress_simple(self):
        """
        Test record compression: Create a folder on custom layer,
        then add a new base layer with the same content. Change
        the object to make it fit new base layer, expect object to
        vanish from custom layer after record.
        """

        # in our custom layer we create a folder with title 'Foobar'
        self.add_folder("Test", "Test")
        self.run("playback", "/Test")
        self.app.Test.title = "Foobar"
        self.run("record", "/")

        # ... then we add a new base layer
        with self.addlayer() as layer:
            shutil.copytree(
                "{}/__root__/Test".format(self.repo.path),  # custom layer!
                "{}/workdir/__root__/Test".format(layer),  # new base layer!
            )
            # now create the standard Test folder titled 'Something
            meta = zodbsync.mod_format({"title": "Something", "type": "Folder"})
            with open(f"{layer}/workdir/__root__/Test/__meta__", "w") as f:
                f.write(meta)
            self.run("playback", "/")

            # still 'Foobar' - custom layer wins
            assert self.app.Test.title == "Foobar"

            # now really switch to 'Something' via app
            self.app.Test.title = "Something"

            # ... and record. should remove customized
            # Test folder aka compress
            self.run("record", "/")
            assert not os.path.isdir(os.path.join(self.repo.path, "__root__/Test"))

    @pytest.mark.parametrize("recurse", [True, False])
    def test_layer_playback(self, recurse):
        """
        Set up a base layer, add a path there and play it back.
        """
        self.add_folder("Test")
        with self.addlayer() as layer:
            src = "{}/__root__".format(self.repo.path)
            tgt = "{}/workdir/__root__".format(layer)
            os.rename(src + "/Test", tgt + "/Test")
            cmd = ["playback", "/Test"]
            if not recurse:
                cmd.append("--no-recurse")
            self.run(*cmd)
            assert "Test" in self.app.objectIds()

    def test_layer_playback_frozen_deleted(self):
        """
        Set up a base layer with a folder, but mask it as deleted in the upper
        layer.
        """
        self.add_folder("Test")
        with self.addlayer() as layer:
            src = "{}/__root__".format(self.repo.path)
            tgt = "{}/workdir/__root__".format(layer)
            shutil.copytree(src + "/Test", tgt + "/Test")
            with open("{}/__frozen__".format(src), "w"):
                pass
            os.remove(src + "/Test/__meta__")
            self.run("playback", "/Test")
            assert "Test" not in self.app.objectIds()

    def test_layer_playback_combined(self):
        """
        Set up a complex hierarchy with two layers and one path being frozen
        and providing different subobjects, one path being merged while also
        changing the object itself and one path being merged without changing
        the object itself.
        """
        for folder in ["Test1", "Test2", "Test3"]:
            self.add_folder(folder)
            for sub in ["Sub1", "Sub2"]:
                self.add_folder(sub, parent=folder)
        with self.addlayer() as layer:
            root = os.path.join(self.repo.path, "__root__")
            # Move current structure into lower layer
            os.rename(root, os.path.join(layer, "workdir/__root__"))
            # Create a sparse structure in top layer
            files = [
                "Test1/__frozen__",
                "Test1/__meta__",
                "Test1/Sub3/__meta__",
                "Test2/__meta__",
                "Test2/Sub3/__meta__",
                "Test3/Sub3/__meta__",
            ]
            meta = """[
                ('props', []),
                ('title', 'overwritten'),
                ('type', 'Folder'),
            ]"""
            for file in files:
                dirname, fname = file.rsplit("/", 1)
                os.makedirs(os.path.join(root, dirname), exist_ok=True)
                with open(os.path.join(root, file), "w") as f:
                    if fname == "__meta__":
                        f.write(meta)

            self.run("playback", "/")
        assert self.app.Test1.objectIds() == ["Sub3"]
        assert self.app.Test2.objectIds() == ["Sub1", "Sub2", "Sub3"]
        assert self.app.Test3.objectIds() == ["Sub1", "Sub2", "Sub3"]
        assert self.app.Test2.title == "overwritten"
        assert self.app.Test3.title == ""

    def test_layer_record(self):
        """
        Add an object and move it to the lower layer. Record again. The object
        must not be added to the top layer since it is already present in the
        lower layer.
        """
        self.add_folder("Test")
        self.run("playback", "/Test")
        self.run("record", "/Test")
        with self.addlayer() as layer:
            root = [
                os.path.join(layer, "workdir/__root__"),
                os.path.join(self.repo.path, "__root__"),
            ]
            os.rename(os.path.join(root[1], "Test"), os.path.join(root[0], "Test"))
            self.run("record", "/Test")
            assert not os.path.isdir(os.path.join(root[1], "Test"))

    def test_layer_prune_single_named_layer(self):
        """
        Object exists only in the named layer (not in Data.FS). Recording
        should remove the named-layer directory without creating __deleted__.
        """
        self.add_folder("Test")
        self.add_folder("Sub", parent="Test")
        with self.addlayer() as layer:
            srcroot = os.path.join(self.repo.path, "__root__")
            tgtroot = os.path.join(layer, "workdir/__root__")
            os.rename(os.path.join(srcroot, "Test"), os.path.join(tgtroot, "Test"))
            self.run("record", "/")
            # Named-layer dir removed; no __deleted__ in fallback
            assert not os.path.isdir(os.path.join(tgtroot, "Test"))
            assert not os.path.isdir(os.path.join(srcroot, "Test"))

    def test_layer_prune_multi_layer(self):
        """
        Object exists in both the fallback layer and a named layer (not in
        Data.FS). Recording should place __deleted__ in the topmost (fallback)
        layer to shadow the named-layer copy.
        """
        self.add_folder("Test")
        self.add_folder("Sub", parent="Test")
        with self.addlayer() as layer:
            srcroot = os.path.join(self.repo.path, "__root__")
            tgtroot = os.path.join(layer, "workdir/__root__")
            shutil.copytree(
                os.path.join(srcroot, "Test"), os.path.join(tgtroot, "Test")
            )
            self.run("record", "/")
            # Fallback layer gets __deleted__ to shadow the named-layer copy
            assert os.path.exists(os.path.join(srcroot, "Test/__deleted__"))

    def test_layer_prune_frozen_masks_lower(self):
        """
        Object exists in both the fallback layer (with __frozen__) and a named
        layer. The frozen marker in the fallback layer makes the named layer
        invisible for that subtree. Pruning should treat this as single-layer
        (only fallback counts) and delete from fallback — NOT create __deleted__.
        """
        self.add_folder("Test")
        self.add_folder("Sub", parent="Test")
        with self.addlayer() as layer:
            srcroot = os.path.join(self.repo.path, "__root__")
            tgtroot = os.path.join(layer, "workdir/__root__")
            # Copy Test into named layer (so it exists in both layers)
            shutil.copytree(
                os.path.join(srcroot, "Test"), os.path.join(tgtroot, "Test")
            )
            # Freeze Test in the fallback layer (marks named layer as invisible)
            with open(os.path.join(srcroot, "Test/__frozen__"), "w"):
                pass
            self.run("record", "/")
            # Frozen masks named layer -> only 1 visible layer (fallback) ->
            # single-layer delete, no __deleted__ marker
            assert not os.path.isdir(os.path.join(srcroot, "Test"))
            assert not os.path.exists(os.path.join(srcroot, "Test/__deleted__"))

    def test_layer_record_prune(self):
        """
        Use a setup with two layers. Add a folder and record it to the custom
        layer. Remove the folder and record again - check that the subfolder is
        actually deleted and not marked with __deleted__.
        """
        self.app.manage_addFolder(id="Test")
        self.run("record", "/")
        with self.addlayer() as layer:
            os.rename(
                os.path.join(self.repo.path, "__root__/__meta__"),
                os.path.join(layer, "workdir/__root__/__meta__"),
            )
            self.run("record", "/")
        assert not os.path.isdir(os.path.join(self.repo.path, "__root__/Test"))

    def test_layer_watch_rename(self):
        """
        Rename an object in the Data.FS that is recorded in a lower layer.
        The renamed object keeps the layer assignment of its OID (rule 1).
        """
        with self.addlayer() as layer:
            os.rename(
                os.path.join(self.repo.path, "__root__/index_html"),
                os.path.join(layer, "workdir/__root__/index_html"),
            )
            watcher = self.mkrunner("watch")
            with self.runner.sync.tm:
                del self.app.index_html.zodbsync_layer
            watcher.setup()

            # Somehow, we need to initialize the connection here and can not
            # use the fixture, otherwise we are not logged in (probably some
            # interference with addlayer resetting the original connection)
            with self.newconn() as conn:
                with conn.tm:
                    conn.app.manage_renameObject("index_html", "something")
            watcher.step()
            # index_html was only in the named layer; single-layer prune removes
            # it directly without creating __deleted__ in the fallback layer
            assert not os.path.isdir(os.path.join(layer, "workdir/__root__/index_html"))
            assert not os.path.isdir(
                os.path.join(self.repo.path, "__root__/index_html")
            )
            # object was in named layer (setup recorded it there via rule 2);
            # after rename the OID keeps zodbsync_layer so rule 1 routes to named
            assert os.path.exists(
                os.path.join(layer, "workdir/__root__/something/__meta__")
            )

    def test_layer_watch_paste(self):
        """
        Set up two folders, where one has a subfolder, both in the lower layer.
        Cut the subfolder and paste it into the other folder, checking the
        result. Then cut it again and paste it into its original place and
        check that.
        """
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Test1")
            self.app.manage_addFolder(id="Test2")
            self.app.Test1.manage_addFolder(id="Sub")
        self.run("record", "/")
        with self.addlayer() as layer:
            src = os.path.join(self.repo.path, "__root__")
            tgt = os.path.join(layer, "workdir/__root__")
            os.rmdir(tgt)
            os.rename(src, tgt)
            os.mkdir(src)
            watcher = self.mkrunner("watch")
            with self.runner.sync.tm:
                del self.app.Test1.zodbsync_layer
                del self.app.Test2.zodbsync_layer
                del self.app.Test1.Sub.zodbsync_layer
            watcher.setup()
            with self.newconn() as conn:
                with conn.tm:
                    cp = conn.app.Test1.manage_cutObjects(["Sub"])
                    conn.app.Test2._pasteObjects(cp)
            layer_root = "{}/workdir/__root__".format(layer)
            # Sub was only in named layer; single-layer prune removes it from
            # named Test1 directly; no __deleted__ in fallback layer.
            self.watcher_step_until(
                watcher,
                lambda: (
                    os.path.exists(os.path.join(layer_root, "Test2/Sub/__meta__"))
                    and not os.path.isdir(os.path.join(layer_root, "Test1/Sub"))
                ),
            )
            with self.newconn() as conn:
                with conn.tm:
                    cp = conn.app.Test2.manage_cutObjects(["Sub"])
                    conn.app.Test1._pasteObjects(cp)

            # After paste back: named Test2/Sub removed (single layer), Sub
            # re-recorded to named Test1/Sub.
            self.watcher_step_until(
                watcher,
                lambda: (
                    not os.path.isdir(os.path.join(layer_root, "Test2/Sub"))
                    and os.path.exists(os.path.join(layer_root, "Test1/Sub/__meta__"))
                ),
            )

    def test_layer_recreate_deleted(self):
        """
        Object recorded to fallback, then moved to named layer (single-layer).
        Delete from ZODB: named-layer dir is removed, no __deleted__ created.
        Recreate in ZODB: re-recorded to named layer, fallback stays clean.
        """
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Test")

        with self.addlayer() as layer:
            self.run("record", "/Test")
            root = os.path.join(self.repo.path, "__root__")
            named_root = os.path.join(layer, "workdir/__root__")
            os.rename(
                os.path.join(root, "Test"),
                os.path.join(named_root, "Test"),
            )
            self.app.manage_delObjects(ids=["Test"])
            self.run("record", "/")
            # Single-layer: named dir removed, no __deleted__ in fallback
            assert not os.path.isdir(os.path.join(named_root, "Test"))
            assert not os.path.isdir(os.path.join(root, "Test"))
            self.app.manage_addFolder(id="Test")
            self.run("record", "/Test")
            # Recreated: root.__meta__ is in fallback (rule 3), so Test goes there
            assert os.path.exists(os.path.join(root, "Test/__meta__"))
            assert not os.path.isdir(os.path.join(named_root, "Test"))

    def test_layer_remove_subfolder(self):
        """
        Set up a folder with a subfolder, both only in the named layer.
        Remove the subfolder. Single-layer prune removes Sub from named layer
        directly; no __deleted__ marker is created in the fallback layer.
        """
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Test")
            self.app.Test.manage_addFolder(id="Sub")

        with self.addlayer() as layer:
            self.run("record", "/")
            root = os.path.join(self.repo.path, "__root__")
            named_root = os.path.join(layer, "workdir/__root__")
            os.rename(
                os.path.join(root, "Test"),
                os.path.join(named_root, "Test"),
            )
            with self.runner.sync.tm:
                self.app.Test.manage_delObjects(ids=["Sub"])
            self.run("record", "/")
            # Test still in named layer (still in ZODB); fallback has no Test
            assert not os.path.exists(os.path.join(root, "Test/__meta__"))
            assert not os.path.exists(os.path.join(root, "Test/Sub/__meta__"))
            # Sub deleted from named layer; no __deleted__ in fallback
            assert not os.path.isdir(os.path.join(named_root, "Test/Sub"))
            assert not os.path.isdir(os.path.join(root, "Test/Sub"))

    def test_layer_update(self, caplog):
        """
        Set up a layer, and register it. Change something in the layer and use
        layer-update to play back the changed object.
        """
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Test")
        with self.addlayer() as layer:
            self.run("record", "/")
            ident = self.runner.sync.layers[-1]["ident"]
            src = os.path.join(self.repo.path, "__root__")
            tgt = os.path.join(layer, "source/__root__")
            os.rmdir(tgt)
            os.rename(src, tgt)
            os.mkdir(src)
            self.run("layer-init", "*")
            with open(os.path.join(tgt, "Test/__meta__"), "w") as f:
                f.write(zodbsync.mod_format({"title": "Changed", "type": "Folder"}))
            self.run("layer-update", ident)
            assert "Conflict with object" not in caplog.text
            assert self.app.Test.title == "Changed"

    def test_keep_acl(self):
        """
        Make sure deletions on top level acl_users are NOT synced into
        Data.fs

        User folders living somewhere else in the application may be
        deleted though.
        """
        acl_path = os.path.join(
            self.repo.path,
            "__root__",
            "acl_users",
        )
        shutil.rmtree(acl_path)
        self.run("playback", "/")

        # this playback will fail horribly if acl_users is gone!
        self.run("playback", "/")

        # make sure acl_users in toplevel is still present
        assert "acl_users" in self.app.objectIds()

        # now create dummy module with its own acl_users folder
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="some_module")
            self.app.some_module.manage_addUserFolder()

        self.run("record", "/")

        assert "acl_users" in self.app.some_module.objectIds()

        module_acl = os.path.join(
            self.repo.path,
            "__root__",
            "some_module",
            "acl_users",
        )
        shutil.rmtree(module_acl)
        self.run("playback", "/")
        assert "acl_users" not in self.app.some_module.objectIds()

    def test_keep_acl_norecurse(self):
        """
        test_keep_acl but slightly altered for norecurse,
        aka playing back single objects instead of the whole
        object tree
        """
        acl_path = os.path.join(
            self.repo.path,
            "__root__",
            "acl_users",
        )
        shutil.rmtree(acl_path)
        self.run("playback", "--no-recurse", "/acl_users")

        # make sure acl_users in toplevel is still present
        assert "acl_users" in self.app.objectIds()

        # now create dummy module with its own acl_users folder
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="some_module")
            self.app.some_module.manage_addFolder(id="something")
            self.app.some_module.manage_addUserFolder()

        self.run("record", "/")

        assert "acl_users" in self.app.some_module.objectIds()

        module_acl = os.path.join(
            self.repo.path,
            "__root__",
            "some_module",
            "acl_users",
        )
        shutil.rmtree(module_acl)
        self.run(
            "playback",
            "--no-recurse",
            "/some_module",
            "/some_module/acl_users",
        )
        assert "acl_users" not in self.app.some_module.objectIds()

    def test_layer_update_warn(self, caplog):
        """
        Set up a layer and initialize it. Change an object that is provided by
        this layer and record it (now routes to named layer via rule 2). Update
        the base layer so that the object changes and ToDelete is removed.
        Verify no AttributeError occurs and that playback applies the source
        changes to the ZODB (Test uploaded, ToDelete removed).
        """
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Test")
            self.app.manage_addFolder(id="ToDelete")
            self.app.ToDelete.manage_addFolder(id="Sub")
        with self.addlayer() as layer:
            self.run("record", "/")
            ident = self.runner.sync.layers[-1]["ident"]
            src = os.path.join(self.repo.path, "__root__")
            tgt = os.path.join(layer, "source/__root__")
            os.rmdir(tgt)
            os.rename(src, tgt)
            os.mkdir(src)
            self.run("layer-init", "*")
            with self.runner.sync.tm:
                self.app.Test._setProperty("nav_hidden", True, "boolean")
                self.app.ToDelete.Sub._setProperty("nav_hidden", True, "boolean")
            self.run("record", "/")
            with open(os.path.join(tgt, "Test/__meta__"), "w") as f:
                f.write(zodbsync.mod_format({"title": "Changed", "type": "Folder"}))
            shutil.rmtree(os.path.join(tgt, "ToDelete"))
            self.run("layer-update", ident)
            # With issue-3 layer routing, edits go to named layer (not custom),
            # so no custom-layer conflict fires. Verify no errors and that
            # playback happened (Test uploaded, ToDelete removed from ZODB).
            assert "AttributeError" not in caplog.text
            assert "Uploading /Test/" in caplog.text
            assert "Removing object /ToDelete/" in caplog.text

    def test_layer_change_into_top(self):
        """
        After record, a changed object whose __meta__ is in a named layer
        must be written back to that named layer (rule 2 on first record,
        rule 1 on subsequent records).
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")

        with self.addlayer() as layer:
            self.run("record", "/blob")
            shutil.move(
                "{}/__root__/blob".format(self.repo.path),
                "{}/workdir/__root__/blob".format(layer),
            )
            with self.runner.sync.tm:
                del self.app.blob.zodbsync_layer
                self.app.blob.manage_edit(
                    filedata="text_content", content_type="text/plain", title="BLOB"
                )
            self.run("record", "/")
            root = os.path.join(self.repo.path, "__root__")
            # custom layer must NOT have blob
            assert not os.path.exists(os.path.join(root, "blob/__meta__"))
            assert not os.path.exists(os.path.join(root, "blob/__source__.txt"))
            # named layer has the new content
            layer_root = "{}/workdir/__root__/blob/__source__.txt".format(layer)
            with open(layer_root) as f:
                assert f.read() == "text_content"

    def test_layer_playback_hook(self):
        """
        Set up two layers. Pick a commit that marks an object as __deleted__ in
        the top layer. Check that the playback hook script gets the normalized
        object paths and not the specific files.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")

        root = "{}/__root__".format(self.repo.path)
        with self.addlayer() as layer:
            self.run("record", "/blob")
            shutil.move(
                "{}/blob".format(root),
                "{}/workdir/__root__/blob".format(layer),
            )
            os.mkdir("{}/blob".format(root))
            with open("{}/blob/__deleted__".format(root), "w"):
                pass
            self.gitrun("add", ".")
            self.gitrun("commit", "-m", "delete blob")
            commid = self.get_head_id()
            self.gitrun("reset", "--hard", "HEAD~")
            output = "{}/playback_hook.out".format(self.zeo.path)
            playback_hook = self.addscript(
                "playback_hook",
                "cat > {}".format(output),
                "echo '[]'",
            )
            with self.appendtoconf('playback_hook = "{}"'.format(playback_hook)):
                self.run("pick", commid)
            with open(output) as f:
                assert {"paths": ["/blob/"]} == json.loads(f.read())

    def test_layer_tar(self):
        """
        Perform a layer-init and layer-update from a tar file source.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            subprocess.run(
                ["tar", "cf", f"{layer}/source/__root__.tar", "blob"],
                cwd=f"{self.repo.path}/__root__",
                check=True,
            )
            os.rmdir(f"{layer}/source/__root__")
            self.run("layer-init", "*")
            assert os.listdir(f"{layer}/workdir/__root__") == ["blob"]
            # Record to remove from fallback layer
            self.run("record", "/")
            assert "blob" not in os.listdir(f"{self.repo.path}/__root__")
            # Now change the file in the TAR file and run layer-update
            shutil.copytree(
                f"{layer}/workdir/__root__/blob",
                f"{layer}/blob",
            )
            with open(f"{layer}/blob/__source__.txt", "w") as f:
                f.write("changed")
            subprocess.run(
                ["tar", "cf", f"{layer}/source/__root__.tar", "blob"],
                cwd=layer,
                check=True,
            )
            self.run("layer-update", "*")
            assert str(self.app.blob) == "changed"

    def test_layer_update_2phase_failed(self):
        """
        Perform layer-update with a two-phase playback where a command at the
        end of the first phase fails. Check that the rollback is performed
        correctly.
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        playback_cmd = self.addscript("playback_cmd", "false")

        playback_hook = self.addscript(
            "playback_hook",
            "echo '{}'".format(
                json.dumps(
                    [
                        {
                            "paths": ["/blob"],
                            "cmd": playback_cmd,
                        }
                    ]
                )
            ),
        )
        with self.appendtoconf('playback_hook = "{}"'.format(playback_hook)):
            with self.addlayer() as layer:
                self.run("record", "/")
                src = f"{self.repo.path}/__root__/blob"
                tgt = f"{layer}/source/__root__/blob"
                os.rename(src, tgt)
                self.run("layer-init", "*")
                with open(f"{tgt}/__source__.txt", "w") as f:
                    f.write("changed")
                with pytest.raises(AssertionError):
                    self.run("layer-update", "*")
                assert str(self.app.blob) == ""

    def test_layer_info_datafs(self):
        """
        Validate the correct writing and clearing of the layer ident
        in the Data.FS
        """
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")

        with self.addlayer() as layer:
            self.run("record", "/blob")
            assert getattr(self.app.blob, "zodbsync_layer", None) == ""
            # Move file to layer; clear attr so rule 2 detects new FS location
            shutil.move(
                "{}/__root__/blob".format(self.repo.path),
                "{}/workdir/__root__/blob".format(layer),
            )
            with self.runner.sync.tm:
                del self.app.blob.zodbsync_layer
            self.run("record", "/")
            assert getattr(self.app.blob, "zodbsync_layer") is not None
            # Change file in Data.FS and verify that layer info is preserved
            with self.runner.sync.tm:
                self.app.blob.manage_edit(
                    filedata="text_content", content_type="text/plain", title="BLOB"
                )
            self.run("record", "/")
            # rule 1 routes back to named layer because zodbsync_layer was set above
            ident = self.runner.sync.layers[-1]["ident"]
            assert getattr(self.app.blob, "zodbsync_layer") == ident

    def test_layer_record_rule4_fallback_custom(self):
        """Rule 4: no zodbsync_layer, no FS presence, no parent layer -> custom."""
        with self.addlayer():
            self.run("record", "/")  # initialise runner with layer config
            with self.runner.sync.tm:
                self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(custom_meta)
            assert getattr(self.app.blob, "zodbsync_layer", None) == ""

    def test_layer_record_rule2_fs_presence(self):
        """Rule 2: existing __meta__ in named layer -> record writes there."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")  # custom layer
            shutil.move(
                "{}/__root__/blob".format(self.repo.path),
                "{}/workdir/__root__/blob".format(layer),
            )
            # rule 2 fires: own zodbsync_layer absent, FS now in named layer
            with self.runner.sync.tm:
                del self.app.blob.zodbsync_layer
                self.app.blob.manage_edit(
                    filedata="new_content", content_type="text/plain", title=""
                )
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            named_src = "{}/workdir/__root__/blob/__source__.txt".format(layer)
            assert not os.path.exists(custom_meta)
            with open(named_src) as f:
                assert f.read() == "new_content"

    def test_layer_record_rule1_zodbsync_layer(self):
        """Rule 1: obj.zodbsync_layer set -> record writes to that named layer."""
        with self.addlayer() as layer:
            self.mkrunner("record")  # init runner with layer config
            ident = self.runner.sync.layers[-1]["ident"]
            with self.runner.sync.tm:
                self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
                self.app.blob.zodbsync_layer = ident  # explicitly assign
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            named_meta = "{}/workdir/__root__/blob/__meta__".format(layer)
            assert not os.path.exists(custom_meta)
            assert os.path.exists(named_meta)

    def test_layer_record_rule3_parent_layer(self):
        """Rule 3: parent __meta__ in named layer -> new child lands in same layer."""
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Folder")
        with self.addlayer() as layer:
            self.run("record", "/Folder")
            shutil.move(
                "{}/__root__/Folder".format(self.repo.path),
                "{}/workdir/__root__/Folder".format(layer),
            )
            # re-record so Folder.zodbsync_layer gets set to named ident
            self.run("record", "/Folder")
            # now create child under Folder
            with self.runner.sync.tm:
                self.app.Folder.manage_addFolder(id="Child")
            self.run("record", "/Folder/Child")
            custom_child = os.path.join(
                self.repo.path, "__root__/Folder/Child/__meta__"
            )
            named_child = "{}/workdir/__root__/Folder/Child/__meta__".format(layer)
            assert not os.path.exists(custom_child)
            assert os.path.exists(named_child)
            ident = self.runner.sync.layers[-1]["ident"]
            assert getattr(self.app.Folder.Child, "zodbsync_layer") == ident

    def test_layer_watch_rule1_into_named_layer(self):
        """Watch rule 1: zodbsync_layer set -> watcher writes to named layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            shutil.move(
                "{}/__root__/blob".format(self.repo.path),
                "{}/workdir/__root__/blob".format(layer),
            )
            with self.runner.sync.tm:
                del self.app.blob.zodbsync_layer
            self.run("record", "/blob")  # rule 2 detects named layer, sets attr

            watcher = self.mkrunner("watch")
            watcher.setup()
            with self.newconn() as conn:
                with conn.tm:
                    conn.app.blob.manage_edit(
                        filedata="watched_content", content_type="text/plain", title=""
                    )
            watcher.step()
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            named_src = "{}/workdir/__root__/blob/__source__.txt".format(layer)
            assert not os.path.exists(custom_meta)
            with open(named_src) as f:
                assert f.read() == "watched_content"

    def test_layer_watch_rule3_new_child_inherits_parent(self):
        """Watch rule 3: parent in named layer -> new child written to same layer."""
        with self.runner.sync.tm:
            self.app.manage_addFolder(id="Folder")
        with self.addlayer() as layer:
            self.run("record", "/Folder")
            shutil.move(
                "{}/__root__/Folder".format(self.repo.path),
                "{}/workdir/__root__/Folder".format(layer),
            )
            self.run("record", "/Folder")  # sets Folder.zodbsync_layer

            watcher = self.mkrunner("watch")
            watcher.setup()
            with self.newconn() as conn:
                with conn.tm:
                    conn.app.Folder.manage_addFolder(id="Child")
            watcher.step()
            custom_child = os.path.join(
                self.repo.path, "__root__/Folder/Child/__meta__"
            )
            named_child = "{}/workdir/__root__/Folder/Child/__meta__".format(layer)
            assert not os.path.exists(custom_child)
            assert os.path.exists(named_child)

    def test_layer_divergence_record(self):
        """Divergence: file in fallback, zodbsync_layer=named -> moves to named."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(custom_meta)
            ident = self.runner.sync.layers[-1]["ident"]
            with self.runner.sync.tm:
                self.app.blob.zodbsync_layer = ident
            self.run("record", "/blob")
            named_meta = "{}/workdir/__root__/blob/__meta__".format(layer)
            assert os.path.exists(named_meta)
            assert not os.path.exists(custom_meta)

    def test_layer_divergence_watch(self):
        """Divergence: file in fallback, zodbsync_layer set via watch -> named."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(custom_meta)
            ident = self.runner.sync.layers[-1]["ident"]

            watcher = self.mkrunner("watch")
            watcher.setup()
            with self.newconn() as conn:
                with conn.tm:
                    conn.app.blob.zodbsync_layer = ident
            watcher.step()
            named_meta = "{}/workdir/__root__/blob/__meta__".format(layer)
            assert os.path.exists(named_meta)
            assert not os.path.exists(custom_meta)

    def test_layer_divergence_clears_frozen(self):
        """Divergence move removes __frozen__ marker from old layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            custom_blob_dir = os.path.join(self.repo.path, "__root__/blob")
            frozen = os.path.join(custom_blob_dir, "__frozen__")
            with open(frozen, "wb"):
                pass
            ident = self.runner.sync.layers[-1]["ident"]
            with self.runner.sync.tm:
                self.app.blob.zodbsync_layer = ident
            self.run("record", "/blob")
            assert not os.path.exists(frozen)
            named_meta = "{}/workdir/__root__/blob/__meta__".format(layer)
            assert os.path.exists(named_meta)

    def test_fail_when_meta_is_missing(self):
        """
        Check that playing back a structure where no layer has a meta file for
        a given folder does not work.
        """
        root = f"{self.repo.path}/__root__"
        os.mkdir(f"{root}/newfolder")
        os.mkdir(f"{root}/newobj")
        with open(f"{root}/newobj/__source__.py", "w"):
            pass
        with pytest.raises(AssertionError):
            self.run("playback", "/")

    def test_fail_when_meta_missing_layers(self):
        """
        Check that playing back a structure where no layer has a meta file for
        a given folder does not work (multi-layer).
        """
        with self.addlayer() as layer:
            os.mkdir(f"{self.repo.path}/__root__/newfolder")
            os.mkdir(f"{layer}/workdir/__root__/newfolder")
            with pytest.raises(AssertionError):
                self.run("playback", "/")

    def test_nofail_deleted(self):
        """
        If an object is removed from the base layer that is already marked in the
        custom layer as __deleted__, this does *not* yield an error because there is
        no __meta__ file in any layer
        """
        with self.addlayer():
            os.mkdir(f"{self.repo.path}/__root__/delfolder/")
            with open(f"{self.repo.path}/__root__/delfolder/__deleted__", "w"):
                pass
            self.run("playback", "/")

    def test_move_single_object(self):
        """Move a single object to a named layer; verify FS state and zodbsync_layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(custom_meta)
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("move", "/blob", ident)
            named_meta = f"{layer}/workdir/__root__/blob/__meta__"
            assert os.path.exists(named_meta)
            assert not os.path.exists(custom_meta)
            assert getattr(self.app.blob, "zodbsync_layer") == ident

    def test_move_no_recurse(self):
        """--no-recurse moves only the named object, not its children."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Folder")
            self.app.Folder.manage_addProduct["OFSP"].manage_addFile(id="child")
        with self.addlayer() as layer:
            self.run("record", "/Folder")
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("move", "--no-recurse", "/Folder", ident)
            named_folder = f"{layer}/workdir/__root__/Folder/__meta__"
            custom_folder = os.path.join(self.repo.path, "__root__/Folder/__meta__")
            custom_child = os.path.join(
                self.repo.path, "__root__/Folder/child/__meta__"
            )
            assert os.path.exists(named_folder)
            assert not os.path.exists(custom_folder)
            # child not moved: still in custom layer
            assert os.path.exists(custom_child)

    def test_move_recursive(self):
        """Recursive move of a subtree moves all descendants to the named layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Folder")
            self.app.Folder.manage_addProduct["OFSP"].manage_addFile(id="child")
        with self.addlayer() as layer:
            self.run("record", "/Folder")
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("move", "/Folder", ident)
            named_folder = f"{layer}/workdir/__root__/Folder/__meta__"
            named_child = f"{layer}/workdir/__root__/Folder/child/__meta__"
            custom_folder = os.path.join(self.repo.path, "__root__/Folder/__meta__")
            custom_child = os.path.join(
                self.repo.path, "__root__/Folder/child/__meta__"
            )
            assert os.path.exists(named_folder)
            assert os.path.exists(named_child)
            assert not os.path.exists(custom_folder)
            assert not os.path.exists(custom_child)
            assert getattr(self.app.Folder, "zodbsync_layer") == ident
            assert getattr(self.app.Folder.child, "zodbsync_layer") == ident

    def test_move_skips_different_layer_child(self):
        """Recursive move skips descendants already assigned to a different layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Folder")
            self.app.Folder.manage_addProduct["OFSP"].manage_addFile(id="child")
        with self.addlayer("00") as layerB:
            with self.addlayer("01") as layerA:
                self.run("record", "/Folder")
                ident_a = self.runner.sync.layers[-2][
                    "ident"
                ]  # layerA (higher priority)
                ident_b = self.runner.sync.layers[-1][
                    "ident"
                ]  # layerB (lower priority)
                # Explicitly assign child to layerB
                with self.runner.sync.tm:
                    self.app.Folder.child.zodbsync_layer = ident_b
                self.run("record", "/Folder/child")
                # Move Folder to layerA; child should be skipped
                self.run("move", "/Folder", ident_a)
                named_folder = f"{layerA}/workdir/__root__/Folder/__meta__"
                named_child_b = f"{layerB}/workdir/__root__/Folder/child/__meta__"
                custom_folder = os.path.join(self.repo.path, "__root__/Folder/__meta__")
                assert os.path.exists(named_folder)
                assert not os.path.exists(custom_folder)
                # child stays in layerB (not moved to layerA)
                assert os.path.exists(named_child_b)
                assert getattr(self.app.Folder.child, "zodbsync_layer") == ident_b

    def test_move_to_custom_layer(self):
        """Move a named-layer object back to the custom layer using empty string."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        with self.addlayer() as layer:
            self.run("record", "/blob")
            ident = self.runner.sync.layers[-1]["ident"]
            # First move to named layer
            self.run("move", "/blob", ident)
            named_meta = f"{layer}/workdir/__root__/blob/__meta__"
            assert os.path.exists(named_meta)
            # Now move back to custom layer
            self.run("move", "/blob", "")
            custom_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(custom_meta)
            assert not os.path.exists(named_meta)
            assert getattr(self.app.blob, "zodbsync_layer") == ""

    def test_copy_uncommitted_changes(self):
        """Copy blob with uncommitted changes to named layer; target gets
        modified content, source resets to HEAD, zodbsync_layer updated."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFile(id="blob")
        self.run("record", "/blob")
        self.gitrun("add", ".")
        self.gitrun("commit", "-m", "add blob")
        with self.runner.sync.tm:
            self.app.blob.manage_edit(
                filedata="modified_content", content_type="text/plain", title=""
            )
        self.run("record", "/blob")
        source_source = os.path.join(self.repo.path, "__root__/blob/__source__.txt")
        assert os.path.exists(source_source)
        with open(source_source) as f:
            assert f.read() == "modified_content"
        with self.addlayer() as layer:
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("copy", "/blob", ident)
            target_meta = f"{layer}/workdir/__root__/blob/__meta__"
            target_source = f"{layer}/workdir/__root__/blob/__source__.txt"
            source_meta = os.path.join(self.repo.path, "__root__/blob/__meta__")
            assert os.path.exists(target_meta)
            assert os.path.exists(target_source)
            assert os.path.exists(source_meta)
            with open(target_source) as f:
                assert f.read() == "modified_content"
            with open(source_source) as f:
                assert f.read() != "modified_content"
            assert getattr(self.app.blob, "zodbsync_layer") == ident

    def test_copy_recursive(self):
        """Recursive copy moves entire subtree to named layer."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Folder")
            self.app.Folder.manage_addProduct["OFSP"].manage_addFile(id="child")
        self.run("record", "/Folder")
        self.gitrun("add", ".")
        self.gitrun("commit", "-m", "add folder")
        with self.runner.sync.tm:
            self.app.Folder.child.manage_edit(
                filedata="modified_child", content_type="text/plain", title=""
            )
        self.run("record", "/Folder")
        child_source = os.path.join(
            self.repo.path, "__root__/Folder/child/__source__.txt"
        )
        assert os.path.exists(child_source)
        with open(child_source) as f:
            assert f.read() == "modified_child"
        with self.addlayer() as layer:
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("copy", "/Folder", ident)
            target_folder = f"{layer}/workdir/__root__/Folder/__meta__"
            target_child = f"{layer}/workdir/__root__/Folder/child/__meta__"
            target_child_source = (
                f"{layer}/workdir/__root__/Folder/child/__source__.txt"
            )
            assert os.path.exists(target_folder)
            assert os.path.exists(target_child)
            assert os.path.exists(target_child_source)
            with open(target_child_source) as f:
                assert f.read() == "modified_child"
            with open(child_source) as f:
                assert f.read() != "modified_child"
            assert getattr(self.app.Folder, "zodbsync_layer") == ident
            assert getattr(self.app.Folder.child, "zodbsync_layer") == ident

    def test_copy_no_recurse(self):
        """--no-recurse copies only the named object, not its children."""
        with self.runner.sync.tm:
            self.app.manage_addProduct["OFSP"].manage_addFolder(id="Folder")
            self.app.Folder.manage_addProduct["OFSP"].manage_addFile(id="child")
        self.run("record", "/Folder")
        self.gitrun("add", ".")
        self.gitrun("commit", "-m", "add folder")
        with self.addlayer() as layer:
            ident = self.runner.sync.layers[-1]["ident"]
            self.run("copy", "--no-recurse", "/Folder", ident)
            target_folder = f"{layer}/workdir/__root__/Folder/__meta__"
            target_child = f"{layer}/workdir/__root__/Folder/child/__meta__"
            source_folder = os.path.join(self.repo.path, "__root__/Folder/__meta__")
            source_child = os.path.join(
                self.repo.path, "__root__/Folder/child/__meta__"
            )
            assert os.path.exists(target_folder)
            assert not os.path.exists(target_child)
            assert os.path.exists(source_folder)
            assert os.path.exists(source_child)
            assert getattr(self.app.Folder, "zodbsync_layer") == ident
