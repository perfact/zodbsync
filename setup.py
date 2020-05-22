# -*- coding: utf-8 -*-

import sys

import setuptools
setuptools.setup(
    name='perfact-zodbsync',
    version='3.14.2',
    description='Zope Recorder and Playback',
    long_description=''' ''',
    author='Ján Jockusch et.al.',
    author_email='devel@perfact.de',
    packages=setuptools.find_packages('.'),
    package_data={
    },
    scripts = [
        'bin/perfact-zoperecord',
        'bin/zodbsync',
    ],
    license='GPLv2',
    platforms=['Linux',],
    install_requires=[
        'filelock',
        'ZODB3' if sys.version_info.major == 2 else 'ZODB',
        'Zope2',
    ],
)
