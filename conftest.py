import pytest
import perfact.zodbsync.tests.environment as env

'''
Create a fixture for tests to use, created from the classes in environment.py
'''


class Namespace():
    def __init__(self, **data):
        self.__dict__.update(data)


@pytest.fixture(scope='class')
def environment():
    myenv = {
        'zeo': env.ZeoInstance(port=9011),
        'repo': env.Repository(),
        'zopeconfig': env.ZopeConfig(zeoport=9011),
    }
    myenv['config'] = env.ZODBSyncConfig(env=myenv)

    yield Namespace(**myenv)

    for item in myenv.values():
        item.cleanup()
