import sys
if sys.version_info.major >= 2:
    unicode = str

# Helper function to generate str from bytes (Python3 only)
def bytes_to_str(value, enc='utf-8'):
    if sys.version_info.major > 2 and isinstance(value, bytes):
        return value.decode(enc, 'ignore')
    return value

def str_to_bytes(value, enc='utf-8'):
    if sys.version_info.major > 2 and isinstance(value, str):
        return value.encode(enc)
    return value


# Functions copied from perfact.generic

def read_pdata(obj):
    '''Avoid authentication problems when reading linked pdata.'''
    if isinstance(obj.data, (bytes, unicode)):
        source = obj.data
    else:
        data = obj.data
        source = ''
        while data is not None:
            source += data.data
            data = data.next
    return source

def simple_html_unquote(value):
    '''Unquote quoted HTML text (minimal version)'''
    tokens = [
        ('&lt;', '<',),
        ('&gt;', '>',),
        ('&quot;', '"',),
        ('&amp;', '&',),
    ]
    for before, after in tokens:
        value = value.replace(before, after)
    return value


# --- Function ported over from the Data.fs
def prop_dict(data):
    props = {}

    # Get the properties from object data
    p = dict(data).get('props', None)
    if not p:
        return props

    # Convert each property into a dictionary
    for item in p:
        pd = dict(item)
        # Extract only the value
        props[pd['id']] = pd['value']

    return props
