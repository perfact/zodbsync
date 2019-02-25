# -*- coding: utf-8 -*-

import sys

from setuptools import setup
setup(name='perfact-zodbsync',
      version='3.11.3',
      description='Zope Recorder and Playback',
      long_description=''' ''',
      author='Ján Jockusch et.al.',
      author_email='devel@perfact.de',
      packages=[
          'perfact',
          'perfact.zodbsync',
      ],
      package_data={
      },
      scripts = [
          'bin/perfact-zoperecord',
          'bin/perfact-zopeplayback',
      ],
      license='GPLv2',
      platforms=['Linux',],
      # install_requires=['distribute',],
      )
