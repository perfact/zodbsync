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

    If a source is provided, updates the object given by path, but only if its
    current source matches that given in orig_source.

    A wrapper script should be placed in the top-level of the ZODB that is only
    accessible for Manager and delegates to this.

    An encoding of None means the sources are Unicode. Other than that, only
    'base64' is supported, which means the sources are interpreted as base64
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


def read_obj(context, path, force_encoding=None):
    '''
    Locate object at given path and return dictionary containing everything of
    interest.

    The 'source' field in the result is always text. If force_encoding is set
    to 'base64' or the source can not be interpreted as UTF-8, it is a base64
    representation of the actual source and the field 'encoding' is also set
    appropriately.
    '''
    obj = context
    for part in path.split('/'):
        if not part:
            continue
        obj = getattr(obj, part)
    result = mod_read(obj)
    result['path'] = '/' + '/'.join(obj.getPhysicalPath())
    result['parent'] = obj.aq_parent

    encoding = force_encoding
    if force_encoding and isinstance(result['source'], str):
        # We need bytes to encode with Base64
        result['source'] = result['source'].encode('utf-8')

    if not force_encoding and isinstance(result['source'], bytes):
        # Try to represent as UTF-8 for better readability.
        # If that does not work, switch to Base64.
        try:
            result['source'] = result['source'].decode('utf-8')
        except UnicodeDecodeError:
            encoding = 'base64'

    if encoding == 'base64':
        result['source'] = b64encode(result['source']).decode('ascii')
        result['encoding'] = encoding

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

    data = read_obj(context, path)

    headers = [
        ('url', url),
        ('path', data['path']),
        ('auth', context.REQUEST._auth),
        ('meta-type', data['type']),
    ]
    encoding = data.get('encoding', None)
    if encoding:
        headers.append(('encoding', encoding))

    props = data.get('props', [])
    for prop in props:
        if ('id', 'content_type') in prop:
            value = [pair for pair in prop if pair[0] == 'value']
            assert len(value), "Invalid property"
            headers.append(('content-type', value[0][1]))
            break

    result = ''.join([
        '{}: {}\n'.format(*header)
        for header in headers
    ]) + '\n' + data['source']

    return result


def update(context, path, source, orig_source, encoding):
    '''
    Update the object with the given source, but only if its current source
    matches the expected orig_source.

    If encoding is set to base64, the sources are considered to be base64
    encoded.
    '''
    assert encoding in (None, 'base64'), "Invalid encoding"

    try:
        data = read_obj(context, path, force_encoding=encoding)
    except AttributeError:
        return {'error': path + ' not found'}

    if data['source'] != orig_source:
        return {'error': 'Object was changed in the meantime. Please reload.'}

    if encoding == 'base64':
        data['source'] = b64decode(source)
    elif encoding is None:
        data['source'] = source
    obj_id = path.rstrip('/').rsplit('/', 1)[-1]
    mod_write(data, parent=data['parent'], obj_id=obj_id)

    return {'success': True}
