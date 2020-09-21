import os
import os.path
import subprocess
import pytest

import perfact.zodbsync.main
import perfact.zodbsync.tests.environment as env


ZEOPORT = 9011


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
        myenv = {
            'zeo': env.ZeoInstance(port=ZEOPORT),
            'repo': env.Repository(),
            'zopeconfig': env.ZopeConfig(zeoport=ZEOPORT),
            'jslib': env.JSLib(),
        }
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

    def runner(self, *cmd):
        '''
        Create runner for given zodbsync command
        '''
        return perfact.zodbsync.main.create_runner(
            ['--config', self.config.path] + list(cmd)
        )

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

    def prepare_pick(self):
        '''
        Prepare a commit containing a new folder that can be picked onto the
        initialized repository. Returns the commit ID.
        '''
        # Add a folder, commit it
        folder = self.repo.path + '/__root__/TestFolder'
        os.mkdir(folder)
        with open(folder + '/__meta__', 'w') as f:
            f.write('''[
                ('props', []),
                ('title', ''),
                ('type', 'Folder'),
            ]''')
        self.gitrun('add', '.')
        self.gitrun('commit', '-m', 'Second commit')
        commit = self.gitoutput('show-ref', '--head', '--hash', 'HEAD').strip()

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

    def test_pick_dryrun(self, capsys):
        '''
        Pick a prepared commit in dry-run mode and check that the folder does
        not exist.
        '''
        commit = self.prepare_pick()
        runner = self.runner('pick', commit, '--dry-run')
        runner.run()

        assert 'TestFolder' not in runner.sync.app.objectIds()

    def test_upload(self):
        '''
        Upload JS library from test environment and check for it in Data.fs
        '''
        return
