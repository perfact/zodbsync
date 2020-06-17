#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json

from . import helpers
from .zodbsync import mod_read, mod_write


def launch(context, script, path, source=None, orig_source=None):
    '''
    Launcher for external edit.

    If called without a source, it is used to create a control file that
    contains the authentication header found in the current request and the
    content of the file that can be found under path.

    If a source is provided, updates the object given by path, but only if the
    current source matches that given in orig_source.

    A wrapper script should be placed in the top-level of the ZODB that is only
    accessible for Manager and delegates to this.
    '''

    resp = context.REQUEST.RESPONSE

    if source is None:
        content_type = 'application/x-perfact-zopeedit'
        result = controlfile(
            context=context,
            path=path,
            url=script.absolute_url(),
        )
    else:
        content_type = 'application/json'
        result = update(
            context=context,
            path=path,
            source=source,
            orig_source=orig_source,
        )
        result = json.dumps(result)

    resp.setHeader('Content-Type', content_type)
    return result


def find_obj(context, path):
    '''
    Locate object at given path
    '''
    obj = context
    for part in path.split('/'):
        if not part:
            continue
        obj = getattr(obj, part)
    return obj


def controlfile(context, path, url):
    '''
    Creates a control file that can be used by an external editor to update the
    contents of an object. The control file contains
    * the entrypoint url (which should be a script in Zope wrapping the
      launcher)
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

    # This is a hack. It would be better if mod_read always returned string
    # sources and the different object types transferred them if necessary
    # (which is only the case in Python 2). But if we change that, we should
    # also no longer store sources with a -utf8 suffix, which would create a
    # large diff. To be discussed.
    data['source'] = helpers.to_string(data['source'])

    props = data.get('props', [])
    for prop in props:
        if ('id', 'content_type') in prop:
            value = [pair for pair in prop if pair[0] == 'value']
            assert len(value), "Invalid property"
            result += 'content-type: {}\n'.format(value[0][1])
            break

    result += 'meta-type: {type}\n\n{source}'.format(**data)
    return result


def update(context, path, source, orig_source):
    '''
    Update the object with the given source, but only if the current source
    matches the expected old_source.
    '''
    obj = find_obj(context, path)
    data = mod_read(obj)

    if helpers.to_string(data['source']) != orig_source:
        return {'error': 'Object was changed'}
    data['source'] = source
    mod_write(obj, data)
    return {'success': True}
