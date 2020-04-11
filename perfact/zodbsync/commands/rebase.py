#!/usr/bin/env python

from ..subcommand import SubCommand

class Rebase(SubCommand):
    ''' Sub-command to rebase the local master branch onto another branch,
    playing back any changed files.'''
