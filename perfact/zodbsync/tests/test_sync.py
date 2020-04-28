import os
import os.path
import subprocess
import pytest

import perfact.zodbsync.main
import perfact.zodbsync.tests.environment as env


ZEOPORT = 9011


class TestSync():
    '''
    All tests defined in this class share the same environment fixture (i.e.,
    same ZEO, same repo etc.)
    '''

    @pytest.fixture(scope='class', autouse=True)
    def environment(self, request):
        '''
        Fixture that is automatically used by all tests. Initializes
        environment and injects the elements of it into the class.
        '''
        myenv = {
            'zeo': env.ZeoInstance(port=ZEOPORT),
            'repo': env.Repository(),
            'zopeconfig': env.ZopeConfig(zeoport=ZEOPORT),
        }
        myenv['config'] = env.ZODBSyncConfig(env=myenv)

        # inject items into class so methods can use them
        for key, value in myenv.items():
            setattr(request.cls, key, value)

        # at this point, all tests are called
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

    def record_all(self, commitmsg=None):
        '''
        Record everything
        '''
        self.runner('record', '/').run()
        if commitmsg is not None:
            self.gitrun('add', '.')
            self.gitrun('commit', '-m', commitmsg)

    def test_record(self):
        '''
        Record everything and make sure acl_users exists.
        '''
        self.record_all()
        assert os.path.isfile(
            self.repo.path + '/__root__/acl_users/__meta__'
        )

    def test_playback(self):
        '''
        Record everything, change /index_html, play it back and check if the
        contents are correct.
        '''
        self.record_all()
        path = self.repo.path + '/__root__/index_html/__source-utf8__.html'
        content = '<html></html>'
        with open(path, 'w') as f:
            f.write(content)
        runner = self.runner('playback', '/index_html')
        runner.run()
        assert runner.sync.app.index_html() == content

    def test_pick(self):
        # Record everything, commit it
        self.record_all(commitmsg='First commit')

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

        # Reset the commit and pick it again
        self.gitrun('reset', '--hard', 'HEAD~')
        runner = self.runner('pick', commit)
        runner.run()

        assert 'TestFolder' in runner.sync.app.objectIds()
