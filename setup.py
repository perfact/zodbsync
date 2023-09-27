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
    version='22.2.4',
    description='Zope Recorder and Playback',
    long_description=''' ''',
    author='Ján Jockusch et.al.',
    author_email='devel@perfact.de',
    packages=setuptools.find_packages('.'),
    package_data={
    },
    entry_points={
        'console_scripts': [
            'perfact-zoperecord=perfact.zodbsync.scripts:zoperecord',
            'perfact-zopeplayback=perfact.zodbsync.scripts:zopeplayback',
            'zodbsync=perfact.zodbsync.scripts:zodbsync',
        ]
    },
    license='GPLv2',
    platforms=['Linux',],
    install_requires=reqs,
)
