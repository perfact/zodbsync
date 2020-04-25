import perfact.zodbsync.main
import os
import os.path


class TestSync():
    '''
    All tests defined in this class share the same environment fixture (i.e.,
    same ZEO, same repo etc.)
    '''
    def test_record(self, environment):
        env = environment
        perfact.zodbsync.main.create_runner([
            '--config', env.config.path, 'record', '/',
        ]).run()
        assert os.path.isfile(env.repo.path + '/__root__/acl_users/__meta__')

    def test_playback(self, environment):
        env = environment
        self.test_record(environment)
        path = env.repo.path + '/__root__/index_html/__source-utf8__.html'
        content = '<html></html>'
        with open(path, 'w') as f:
            f.write(content)
        runner = perfact.zodbsync.main.create_runner([
            '--config', env.config.path, 'playback', '/index_html'
        ])
        runner.run()
        assert runner.sync.app.index_html() == content
