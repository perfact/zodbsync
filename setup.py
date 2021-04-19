# -*- coding: utf-8 -*-

import sys

import setuptools

reqs = ['filelock']
if sys.version_info.major == 2:
    reqs.extend(['ZODB3', 'Zope2'])
else:
    reqs.extend(['ZODB', 'Zope'])

setuptools.setup(
    name='perfact-zodbsync',
    version='4.1.5.dev1',
    description='Zope Recorder and Playback',
    long_description=''' ''',
    author='Ján Jockusch et.al.',
    author_email='devel@perfact.de',
    packages=setuptools.find_packages('.'),
    package_data={
    },
    scripts = [
        'bin/perfact-zoperecord',
        'bin/perfact-zopeplayback',
        'bin/zodbsync',
    ],
    license='GPLv2',
    platforms=['Linux',],
    install_requires=reqs,
)
