#!/usr/bin/env python
# -*- coding: utf-8 -*-

from perfact.zodbsync.zodbsync import mod_read, mod_write

def find_obj(context, path):
    obj = context
    for part in path.split('/'):
        if not part:
            continue
        obj = getattr(context, part)
    return obj

def controlfile(context, path, url):
    '''
    Creates a control file that can be used by an external editor to update the
    contents of an object. The control file contains
    * the entrypoint url (which should be a script in Zope wrapping these methods)
    * an authentication header
    * the path to the object in question
    * the meta_type of the object
    * the source of the object
    '''
    data = (
        ('url', url),
        ('path', path),
        ('auth', context.REQUEST._auth),
    )
    result = ''.join([
        '{}: {}\n'.format(key, value)
        for key, value in data
    ])

    obj = find_obj(context, path)
    data = mod_read(obj)

    result += 'type: {type}\n\n{source}'.format(**data)
    return result
