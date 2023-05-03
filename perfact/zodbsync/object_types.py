#!/usr/bin/env python
from . import helpers
from .object_mixins import MixinModObj


class ModObj(object):
    meta_types = []

    @staticmethod
    def create(obj, data, obj_id):
        return

    @staticmethod
    def read(obj):
        return ()

    @staticmethod
    def write(obj, data):
        return

    @classmethod
    def collect_handlers(cls):
        """
        Create a dictionary mapping the supported meta_types of each class to
        the handler class by recursing into subclasses.
        """
        result = {
            meta_type: cls
            for meta_type in cls.meta_types
        }
        for sub in cls.__subclasses__():
            result.update(sub.collect_handlers())
        return result


class UserFolderObj(ModObj):
    meta_types = ['User Folder', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addUserFolder()

    @staticmethod
    def read(obj):
        users = []
        for user in obj.getUsers():
            users.append((
                user.getUserName(),
                user._getPassword(),
                user.roles,
                user.getDomains(),
            ))
        return {'users': users}

    @staticmethod
    def write(obj, data):
        users = obj.getUsers()
        current_users = [user.getUserName() for user in users]
        target_users = [user[0] for user in data['users']]
        obj._doDelUsers([u for u in current_users if u not in target_users])
        for user in data['users']:
            # according to AccessControl/userfolder.py, an existing user of the
            # same name is simply overwritten by _doAddUser
            obj._doAddUser(
                user[0],  # username
                '',  # password is set separately
                user[2],  # roles
                user[3],  # domains
            )
            # _doAddUser encrypts the given password, but the password in the
            # dump is already encrypted, so we have to set it manually
            obj.getUserById(user[0]).__ = user[1]


class DTMLDocumentObj(ModObj):
    meta_types = ['DTML Document', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addDTMLDocument(id=obj_id)

    @staticmethod
    def read(obj):
        return {
            'source': helpers.to_bytes(obj.raw)
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            data=helpers.to_string(data['source']),
            title=data['title'],
        )


class DTMLMethodObj(DTMLDocumentObj):
    meta_types = ['DTML Method', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addDTMLMethod(id=obj_id)


class DTMLTeXObj(DTMLDocumentObj):
    meta_types = ['DTML TeX', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['DTMLTeX'].manage_addDTMLTeX(id=obj_id)


class ZForceObj(ModObj):
    meta_types = ['ZForce', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['ZForce'].manage_addZForce(
            id=obj_id,
            title='',
            query_id='',
            fields_id='',
        )

    @staticmethod
    def read(obj):
        return {}

    @staticmethod
    def write(obj, data):
        obj.manage_changeProperties(title=data['title'])


class ZSQLMethodObj(ModObj):
    meta_types = ['Z SQL Method', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['ZSQLMethods'].manage_addZSQLMethod(
            id=obj_id,
            title=data['title'],
            connection_id=data['connection_id'],
            arguments=data['args'],
            template=helpers.to_string(data['source']),
        )

    @staticmethod
    def read(obj):
        return {
            'args': obj.arguments_src,
            'connection_id': obj.connection_id,
            'source': helpers.to_bytes(obj.src),
            'advanced': sorted([
                ('connection_hook', obj.connection_hook),
                ('max_rows', obj.max_rows_),
                ('max_cache', obj.max_cache_),
                ('cache_time', obj.cache_time_),
                ('class_name', obj.class_name_),
                ('class_file', obj.class_file_),
            ]),
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            title=data['title'],
            connection_id=data['connection_id'],
            arguments=data['args'],
            template=helpers.to_string(data['source']),
        )

        # Advanced settings
        adv = dict(data['advanced'])
        obj.manage_advanced(**adv)


class ExternalMethodObj(ModObj):
    meta_types = ['External Method', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['ExternalMethod'].manage_addExternalMethod(
            id=obj_id,
            title=data['title'],
            module=data['module'],
            function=data['function'],
        )

    @staticmethod
    def read(obj):
        return {
            'function': obj.function(),
            'module': obj.module(),
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            title=data['title'],
            module=data['module'],
            function=data['function'],
        )


class FileObj(ModObj):
    meta_types = ['File', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addFile(id=obj_id)

    @staticmethod
    def read(obj):
        # XXX Precondition
        # Read chunked source from File/Image objects.
        return {'source': helpers.read_pdata(obj)}

    @staticmethod
    def write(obj, data):
        pd = helpers.prop_dict(data)

        # XXX Precondition?
        obj.manage_edit(
            filedata=data['source'],
            content_type=pd['content_type'],
            title=data['title'],
        )


class ImageObj(FileObj):
    meta_types = ['Image', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addImage(id=obj_id, file='')


class FolderObj(ModObj):
    meta_types = ['Folder', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addFolder(id=obj_id)

    @staticmethod
    def read(obj):
        # Site Access
        try:
            get_ar = obj.manage_addProduct['SiteAccess'].manage_getAccessRule
        except (AttributeError, KeyError):
            get_ar = None
        if get_ar:
            accessrule = get_ar and get_ar()
            if accessrule:
                return {'accessrule': accessrule}

        return {}

    @staticmethod
    def write(obj, data):
        obj.manage_changeProperties(title=data['title'])

        # Access Rule
        accessrule = data.get('accessrule', None)
        if accessrule:
            obj.manage_addProduct['SiteAccess'].manage_addAccessRule(
                method_id=accessrule
            )


class FolderOrderedObj(FolderObj):
    meta_types = ['Folder (Ordered)', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addOrderedFolder(id=obj_id)

    @staticmethod
    def read(obj):
        result = FolderObj.read(obj)
        # ordered folders store their contents to represent the ordering
        result['contents'] = [a[0] for a in obj.objectItems()]
        return result

    @staticmethod
    def write(obj, data):
        obj.manage_changeProperties(title=data['title'])

        # Access Rule
        accessrule = data.get('accessrule', None)
        if accessrule:
            obj.manage_addProduct['SiteAccess'].manage_addAccessRule(
                method_id=accessrule
            )

    @staticmethod
    def fix_order(obj, data):
        # sort children for ordered folders
        contents = data.get('contents', [])
        srv_contents = [a[0] for a in obj.objectItems()]

        # only use contents that are present in the object
        contents = [a for a in contents if a in srv_contents]
        obj.moveObjectsByDelta(contents, -len(contents))


class PageTemplateObj(ModObj):
    meta_types = ['Page Template', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['PageTemplates'].manage_addPageTemplate(
            id=obj_id,
            text=''
        )

    @staticmethod
    def read(obj):
        return {'source': obj._text}

    @staticmethod
    def write(obj, data):
        obj.pt_setTitle(data['title'], 'utf-8')
        obj.write(data['source'])


class RAMCacheManagerObj(ModObj):
    meta_types = ['RAM Cache Manager', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct[
            'StandardCacheManagers'
        ].manage_addRAMCacheManager(id=obj_id)

    @staticmethod
    def read(obj):
        return {
            'settings': sorted(obj.getSettings().items())
        }

    @staticmethod
    def write(obj, data):
        obj.manage_editProps(
            title=data['title'],
            settings=dict(data['settings']),
        )


class AcceleratedHTTPCacheManagerObj(RAMCacheManagerObj):
    meta_types = ['Accelerated HTTP Cache Manager', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct[
            'StandardCacheManagers'
        ].manage_addAcceleratedHTTPCacheManager(id=obj_id)


class ScriptPythonObj(ModObj):
    meta_types = ['Script (Python)', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['PythonScripts'].manage_addPythonScript(
            id=obj_id
        )

    @staticmethod
    def read(obj):
        return {
            'bindings': sorted(
                obj.getBindingAssignments().getAssignedNames().items()
            ),
            'args': obj.params(),
            'source': helpers.to_bytes(obj.body()),
            'proxy_roles': sorted(list(obj._proxy_roles)),
        }

    @staticmethod
    def write(obj, data):
        obj.ZPythonScript_setTitle(title=data['title'])
        obj.ZPythonScript_edit(params=data['args'],
                               body=helpers.to_string(data['source']))
        obj.ZBindings_edit(mapping=dict(data['bindings']))
        obj.manage_proxy(roles=data['proxy_roles'])


class ZPsycopgDAObj(ModObj):
    meta_types = ['Z Psycopg 2 Database Connection',
                  'Z Psycopg Database Connection', ]

    @staticmethod
    def create(obj, data, obj_id):
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZPsycopgDA'].manage_addZPsycopgConnection(
            id=obj_id,
            title=data['title'],
            connection_string=data['connection_string'],
        )

    @staticmethod
    def read(obj):
        # late additions may not yet be everywhere in the Data.fs
        obj_dict = {
            'autocommit': getattr(obj, 'autocommit', False),
            'readonlymode': getattr(obj, 'readonlymode', False),
            'connection_string': obj.connection_string,
            'encoding': obj.encoding,
            'tilevel': obj.tilevel,
            'zdatetime': obj.zdatetime,
        }
        # Place additional parameters into the object dict only if
        # they're set to non-default values
        if hasattr(obj, 'use_tpc') and obj.use_tpc:
            obj_dict['use_tpc'] = obj.use_tpc
        if hasattr(obj, 'datetime_str') and obj.datetime_str:
            obj_dict['datetime_str'] = obj.datetime_str
        return obj_dict

    @staticmethod
    def write(obj, data):
        parameters = {
            'title': data['title'],
            'connection_string': data['connection_string'],
            'zdatetime': data['zdatetime'],
            'tilevel': data['tilevel'],
            'autocommit': data.get('autocommit', False),
            'readonlymode': data.get('readonlymode', False),
            'encoding': data['encoding'],
        }
        if hasattr(obj, 'use_tpc'):
            parameters['use_tpc'] = data.get('use_tpc', False)
        if hasattr(obj, 'datetime_str'):
            parameters['datetime_str'] = data.get('datetime_str', False)
        obj.manage_edit(**parameters)


class ZPyODBCDAObj(ModObj):
    meta_types = ['Z PyODBC Database Connection', ]

    @staticmethod
    def create(obj, data, obj_id):
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZPyODBCDA'].addpyodbcConnectionBrowser(
            id=obj_id,
            title=data['title'],
            connection_string=data['connection_string'],
            auto_commit=data['autocommit'],
            MaxRows=data['maxrows'],
        )

    @staticmethod
    def read(obj):
        return {
            'autocommit': obj.auto_commit,
            'connection_string': obj.connx_string,
            'maxrows': obj.MaxRows,
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            title=data['title'],
            connection_string=data['connection_string'],
            auto_commit=data['autocommit'],
            MaxRows=data['maxrows'],
        )


class ZcxOracleDAObj(ModObj):
    meta_types = ['Z cxOracle Database Connection', ]

    @staticmethod
    def create(obj, data, obj_id):
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZcxOracleDA'].manage_addZcxOracleConnection(
            id=obj_id,
            title=data['title'],
            connection_string=data['connection_string'],
        )

    @staticmethod
    def read(obj):
        return {
            'connection_string': obj.connection_string,
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            title=data['title'],
            connection_string=data['connection_string'],
        )


class ZsapdbDAObj(ModObj):
    meta_types = ['Z sap Database Connection', ]

    @staticmethod
    def create(obj, data, obj_id):
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZsapdbDA'].manage_addZsapdbConnection(
            id=obj_id,
            title=data['title'],
            connection_string=data['connection_string'],
        )

    @staticmethod
    def read(obj):
        return {
            'connection_string': obj.connection_string,
        }

    @staticmethod
    def write(obj, data):
        obj.manage_edit(
            title=data['title'],
            connection_string=data['connection_string'],
        )


class SimpleUserFolderObj(FolderObj):
    meta_types = ['Simple User Folder', ]

    @staticmethod
    def create(obj, data, obj_id):
        obj.manage_addProduct['SimpleUserFolder'].addSimpleUserFolder()


class MailHostObj(ModObj):
    meta_types = ['Mail Host', ]

    @staticmethod
    def create(obj, data, obj_id):
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['MailHost'].manage_addMailHost(
            id=obj_id,
            title=data['title'],
            smtp_host=data['smtp_host'],
            smtp_port=data['smtp_port'],
        )

    @staticmethod
    def read(obj):
        return {
            'smtp_host': obj.smtp_host,
            'smtp_port': obj.smtp_port,
            'smtp_uid': obj.smtp_uid,
            'smtp_pwd': obj.smtp_pwd,
            'force_tls': obj.force_tls,
            'smtp_queue': obj.smtp_queue,
            'smtp_queue_directory': obj.smtp_queue_directory,
        }

    @staticmethod
    def write(obj, data):
        obj.manage_makeChanges(
            title=data['title'],
            smtp_host=data['smtp_host'],
            smtp_port=data['smtp_port'],
            smtp_uid=data.get('smtp_uid', ''),
            smtp_pwd=data.get('smtp_pwd', ''),
            force_tls=data.get('force_tls', False),
            smtp_queue=data.get('smtp_queue', ''),
            smtp_queue_directory=data.get('smtp_queue_directory', ''),
        )


object_handlers = ModObj.collect_handlers()


def mod_implemented_handlers(obj, meta_type):
    """
    Return the handlers of interfaces that the object implements. This always
    includes the one defined from the meta_type and may also include some of
    the mixins.
    """
    return [object_handlers[meta_type]] + [
        cls for cls in MixinModObj.__subclasses__()
        if cls.implements(obj)
    ]
