#!/usr/bin/env python

import json
from base64 import b64encode, b64decode

from .zodbsync import mod_read, mod_write


def launch(context, script, path, source=None, orig_source=None,
           encoding=None):
    '''
    Launcher for external edit.

    If called without a source, it is used to create a control file that
    contains the authentication header found in the current request and the
    content of the file that can be found under path.

    If a source is provided, updates the object given by path, but only if the
    current source matches that given in orig_source.

    A wrapper script should be placed in the top-level of the ZODB that is only
    accessible for Manager and delegates to this.

    An encoding of None means the sources are Unicode. Other than that, only
    'b64' is supported, which means the sources are interpreted as base64
    encoded binary data.
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
            encoding=encoding,
        )
        result = json.dumps(result)

    resp.setHeader('Content-Type', content_type)
    return result


def read_obj(context, path, force_binary=False):
    '''
    Locate object at given path and return dictionary containing everything of
    interest.

    If force_binary is not set, we attempt to return a string.
    '''
    obj = context
    for part in path.split('/'):
        if not part:
            continue
        obj = getattr(obj, part)
    result = mod_read(obj)
    result['parent'] = obj.aq_parent

    if force_binary and isinstance(result['source'], str):
        result['source'] = result['source'].encode('utf-8')

    if not force_binary and isinstance(result['source'], bytes):
        try:
            result['source'] = result['source'].decode('utf-8')
        except UnicodeDecodeError:
            pass

    return result


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

    data = read_obj(context, path)
    if isinstance(data['source'], bytes):
        result += 'binary: 1\n'
        data['source'] = b64encode(data['source']).decode('ascii')

    props = data.get('props', [])
    for prop in props:
        if ('id', 'content_type') in prop:
            value = [pair for pair in prop if pair[0] == 'value']
            assert len(value), "Invalid property"
            result += 'content-type: {}\n'.format(value[0][1])
            break

    result += 'meta-type: {type}\n\n{source}'.format(**data)
    return result


def update(context, path, source, orig_source, encoding):
    '''
    Update the object with the given source, but only if the current source
    matches the expected orig_source.

    If binary is set, the sources are interpreted as base64 encoded.
    '''
    assert encoding in (None, 'b64'), "Invalid encoding"

    if encoding == 'b64':
        # Submitted sources are base64. Decode, but keep as bytes
        source = b64decode(source.encode('ascii'))
        orig_source = b64decode(orig_source.encode('ascii'))

    data = read_obj(context, path, force_binary=(encoding is not None))

    if data['source'] != orig_source:
        return {'error': 'Object was changed'}

    data['source'] = source
    obj_id = path.rstrip('/').rsplit('/', 1)[-1]
    mod_write(data, parent=data['parent'], obj_id=obj_id)

    return {'success': True}
