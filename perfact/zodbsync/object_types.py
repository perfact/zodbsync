import sys
import AccessControl.Permission

from perfact.zodbsync.helpers import *

class ModObj:
    meta_types = []

    def implements(self, obj):
        return True

    def create(self, obj, data, obj_id):
        return

    def read(self, obj):
        return ()

    def write(self, obj, data):
        return

    def write_after_recurse_hook(self, obj, data):
        ''' implement if an action is to be performed on a to-be-played-back
        object after recursing into its children. '''
        return

class AccessControlObj(ModObj):
    meta_types = ['AccessControl', ]

    def read(self, obj):
        ac = []

        is_root = obj.isTopLevelPrincipiaApplicationObject
        if is_root:
            ac.append(('is_root', True))

        userdefined_roles = tuple(sorted(obj.userdefined_roles()))
        if userdefined_roles:
            ac.append(('roles', userdefined_roles))

        local_roles = obj.get_local_roles()
        # Ignore local owner role if it is trivial
        local_roles = sorted([
            role for role in local_roles
            if role[1] != ('Owner',)]
        )
        if local_roles:
            ac.append(('local_roles', list(local_roles)))

        try:
            ownerinfo = obj._owner
            ac.append(('owner', ownerinfo))
        except AttributeError:
            pass

        # The object's settings where they differ from the default (acquire)
        try:
            # we can not use permission_settings() since it only yields
            # permissions for currently valid roles - however, in watch mode,
            # we do not have the acquisition context and therefore might not
            # know all roles, so we need to go one step further down, using
            # Permission.getRoles()
            perm_set = obj.ac_inherited_permissions(1)
            perm_set = [
                AccessControl.Permission.Permission(p[0], p[1], obj)
                for p in perm_set
            ]
        except AttributeError:
            perm_set = []

        out = []
        for perm in perm_set:
            roles = perm.getRoles(default=[])
            # for some reason, someone decided to encode whether a permission
            # is acquired by returning either a tuple or a list...
            acquire = isinstance(roles, list)
            roles = list(roles)
            roles.sort()
            if acquire and len(roles) == 0:
                # Does not deviate from default
                continue
            out.append((perm.name, acquire, roles))

        out.sort()
        if out:
            ac.append(('perms', out))

        return ac

    def write(self, obj, data):
        d = dict(data)

        # Create userdef roles
        if d.get('roles', None):
            current_roles = obj.userdefined_roles()
            for role in d['roles']:
                if role not in current_roles:
                    obj._addRole(role)

            toremove = [r for r in current_roles if r not in d['roles']]
            if toremove:
                obj._delRoles(toremove)

        # set local roles
        if d.get('local_roles', None):
            for userid, roles in d['local_roles']:
                obj.manage_setLocalRoles(userid, roles)

        # Permission settings
        # permissions that are not stored are understood to be acquired, with
        # no additional roles being granted this permission
        # An exception is the root application object, which can not acquire
        stored_perms = {
            name: (acquire, roles)
            for name, acquire, roles in d.get('perms', [])
        }
        for role in obj.ac_inherited_permissions(1):
            name = role[0]
            if name in stored_perms:
                roles = stored_perms[name][1]
                if not stored_perms[name][0]:
                    # no acquire, which ist stored in a tuple instead of a list
                    roles = tuple(roles)
            else:
                # the default is to acquire without additional roles - except
                # for the top-level object, where it is not to acquire and
                # allow Manager (read() will usually record all permissions for
                # the top level object, but in case there are new permissions,
                # we need to pick a sane default)
                if obj.isTopLevelPrincipiaApplicationObject:
                    roles = ('Manager',)
                else:
                    roles = []
            AccessControl.Permission.Permission(name, [], obj).setRoles(roles)

        # set ownership
        if 'owner' in d:
            owner = d['owner']
            if isinstance(owner, str):
                # backward compatibility for older behavior, where the
                # corresponding UserFolder was not included
                owner = (['acl_users'], owner)

            obj._owner = d['owner']


class UserFolderObj(ModObj):
    meta_types = ['User Folder', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addUserFolder()
        return

    def read(self, obj):
        users = []
        for user in obj.getUsers():
            users.append((
                user.getUserName(),
                user._getPassword(),
                user.roles,
                user.getDomains(),
                ))
        return [('users', users)]

    def write(self, obj, data):
        d = dict(data)
        users = obj.getUsers()
        current_users = [user.getUserName() for user in users]
        target_users = [user[0] for user in d['users']]
        obj._doDelUsers([u for u in current_users if u not in target_users])
        for user in d['users']:
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
        return


class DTMLDocumentObj(ModObj):
    meta_types = ['DTML Document', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addDTMLDocument(id=obj_id)
        return

    def read(self, obj):
        return [('source', str_to_bytes(simple_html_unquote(str(obj))))]

    def write(self, obj, data):
        d = dict(data)

        obj.manage_edit(
            data=bytes_to_str(d['source']),
            title=d['title'])
        return


class DTMLMethodObj(DTMLDocumentObj):
    meta_types = ['DTML Method', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['OFSP'].manage_addDTMLMethod(id=obj_id)
        return


class DTMLTeXObj(DTMLDocumentObj):
    meta_types = ['DTML TeX', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['DTMLTeX'].manage_addDTMLTeX(id=obj_id)
        return


class ZForceObj(ModObj):
    meta_types = ['ZForce', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['ZForce'].manage_addZForce(
            id=obj_id,
            title='',
            query_id='',
            fields_id='')
        return

    def read(self, obj):
        meta = []
        meta.append(('contents', self.contents(obj)))
        return meta

    def write(self, obj, data):
        d = dict(data)

        obj.manage_changeProperties(title=d['title'])
        return


class ZSQLMethodObj(ModObj):
    meta_types = ['Z SQL Method', ]

    def create(self, obj, data, obj_id):
        d = dict(data)

        obj.manage_addProduct['ZSQLMethods'].manage_addZSQLMethod(
            id=obj_id, title=d['title'],
            connection_id=d['connection_id'],
            arguments=d['args'], template=bytes_to_str(d['source']))
        return

    def read(self, obj):
        meta = []
        args = obj.arguments_src
        meta.append(('args', args))
        connection_id = obj.connection_id
        meta.append(('connection_id', connection_id))
        meta.append(('source', str_to_bytes(obj.src)))

        # Advanced tab
        advanced = {
            'connection_hook': obj.connection_hook,
            'max_rows': obj.max_rows_,
            'max_cache': obj.max_cache_,
            'cache_time': obj.cache_time_,
            'class_name': obj.class_name_,
            'class_file': obj.class_file_,
            }

        adv = list(advanced.items())
        adv.sort()
        meta.append(('advanced', adv))

        return meta

    def write(self, obj, data):
        d = dict(data)

        obj.manage_edit(
            title=d['title'],
            connection_id=d['connection_id'],
            arguments=d['args'],
            template=bytes_to_str(d['source']))

        # Advanced settings
        adv = dict(d['advanced'])
        obj.manage_advanced(**adv)
        return


class ExternalMethodObj(ModObj):
    meta_types = ['External Method', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['ExternalMethod'].manage_addExternalMethod(
            id=obj_id,
            title=d['title'],
            module=d['module'],
            function=d['function'])
        return

    def read(self, obj):
        meta = []
        meta.append(('function', obj.function()))
        meta.append(('module',  obj.module()))
        return meta

    def write(self, obj, data):
        d = dict(data)

        obj.manage_edit(
            title=d['title'],
            module=d['module'],
            function=d['function'])
        return


class FileObj(ModObj):
    meta_types = ['File', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['OFSP'].manage_addFile(id=obj_id)
        return

    def read(self, obj):
        # Read chunked source from File/Image objects.
        source = read_pdata(obj)

        # XXX Precondition

        return [('source', source), ]

    def write(self, obj, data):
        d = dict(data)
        pd = prop_dict(data)

        # XXX Precondition?

        obj.manage_edit(
            filedata=d['source'],
            content_type=pd['content_type'],
            title=d['title'])
        return


class ImageObj(FileObj):
    meta_types = ['Image', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['OFSP'].manage_addImage(id=obj_id, file='')
        return


class FolderObj(ModObj):
    meta_types = ['Folder', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['OFSP'].manage_addFolder(id=obj_id)
        return

    def read(self, obj):
        meta = []

        # Site Access
        try:
            get_ar = obj.manage_addProduct['SiteAccess'].manage_getAccessRule
        except (AttributeError, KeyError):
            get_ar = None
        if get_ar:
            accessrule = get_ar and get_ar()
            if accessrule:
                meta.append(('accessrule', accessrule))

        return meta

    def write(self, obj, data):
        d = dict(data)

        obj.manage_changeProperties(title=d['title'])

        # Access Rule
        accessrule = d.get('accessrule', None)
        if accessrule:
            obj.manage_addProduct['SiteAccess'].manage_addAccessRule(
                method_id=accessrule
            )
        return


class FolderOrderedObj(FolderObj):
    meta_types = ['Folder (Ordered)', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['OFSP'].manage_addOrderedFolder(id=obj_id)
        return

    def read(self, obj):
        meta = []

        # ordered folders store their contents to represent the ordering
        contents = [a[0] for a in obj.objectItems()]
        meta.append(('contents', contents))

        try:
            get_ar = obj.manage_addProduct['SiteAccess'].manage_getAccessRule
        except (KeyError, AttributeError):
            get_ar = None
        accessrule = get_ar and get_ar()
        if accessrule:
            meta.append(('accessrule', accessrule))

        return meta

    def write(self, obj, data):
        d = dict(data)

        obj.manage_changeProperties(title=d['title'])

        # Access Rule
        accessrule = d.get('accessrule', None)
        if accessrule:
            obj.manage_addProduct['SiteAccess'].manage_addAccessRule(
                method_id=accessrule
            )
        return
    
    def write_after_recurse_hook(self, obj, data):
        # sort children for ordered folders
        contents = data.get('contents', [])
        srv_contents = [a[0] for a in obj.objectItems()]

        # only use contents that are present in the object
        contents = [a for a in contents if a in srv_contents]
        obj.moveObjectsByDelta(contents, -len(contents))



class PageTemplateObj(ModObj):
    meta_types = ['Page Template', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['PageTemplates'].manage_addPageTemplate(
            id=obj_id,
            text=''
        )
        return

    def read(self, obj):
        return [('source', obj.read()), ]

    def write(self, obj, data):
        d = dict(data)
        obj.pt_setTitle(d['title'], 'utf-8')
        obj.write(d['source'])
        return


class PropertiesObj(ModObj):
    meta_types = ['Properties', ]

    def implements(self, obj):
        if hasattr(obj, 'aq_explicit'):
            me = obj.aq_explicit
        else:
            me = obj
        return hasattr(me, 'propertyMap')

    def read(self, obj):
        meta = []

        props = obj.propertyMap()

        # Optional: Ignore the "title" property, if it exists
        props = list([a for a in props if a['id'] != 'title'])

        for prop in props:
            prop['value'] = obj.getProperty(prop['id'])
            # Handle inherited properties correctly
            if 'mode' in prop:
                val = getattr(obj, prop['id'])
                del prop['mode']
                prop['value'] = val

        props = [list(a.items()) for a in props]

        # Keep the items sorted and hash-friendly
        for item in props:
            item.sort()
        # Sort the properties
        props.sort()

        if props:
            meta.append(('props', props))

        return meta

    def write(self, obj, data):
        import zExceptions
        d = dict(data)
        props = d.get('props', [])

        new_ids = []
        for prop in props:
            pd = dict(prop)
            new_ids.append(pd['id'])
            if obj.hasProperty(pd['id']):
                continue
            try:
                obj.manage_addProperty(pd['id'], pd['value'], pd['type'])
            except zExceptions.BadRequest as e:
                print("Ignoring error when adding property: "+repr(e))

        # Delete surplus properties
        old_ids = obj.propdict().keys()
        del_ids = [a for a in old_ids if a not in new_ids+['title', ]]
        try:
            obj.manage_delProperties(ids=del_ids)
        except zExceptions.BadRequest as e:
            if str(e) == 'Cannot delete output_encoding':
                print("Ignoring failed attempt to delete output_encoding")
            else:
                raise
        except AttributeError as e:
            if str(e) == 'alt':
                print("Ignoring AttributeError on property deletion")
            else:
                raise
        pd = prop_dict(data)
        obj.manage_changeProperties(**pd)
        return


class RAMCacheManagerObj(ModObj):
    meta_types = ['RAM Cache Manager', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct[
                'StandardCacheManagers'
        ].manage_addRAMCacheManager(id=obj_id)
        return

    def read(self, obj):
        meta = []

        settings = list(obj.getSettings().items())
        settings.sort()

        meta.append(('settings', settings))

        return meta

    def write(self, obj, data):
        d = dict(data)
        settings = dict(d['settings'])

        obj.manage_editProps(
            title=d['title'],
            settings=settings)
        return


class AcceleratedHTTPCacheManagerObj(RAMCacheManagerObj):
    meta_types = ['Accelerated HTTP Cache Manager', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct[
                'StandardCacheManagers'
                ].manage_addAcceleratedHTTPCacheManager(id=obj_id)
        return


class ScriptPythonObj(ModObj):
    meta_types = ['Script (Python)', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        obj.manage_addProduct['PythonScripts'].manage_addPythonScript(
                id=obj_id
        )
        return

    def read(self, obj):
        meta = []

        bindmap = list(obj.getBindingAssignments().getAssignedNames().items())
        bindmap.sort()
        meta.append(('bindings', bindmap))
        meta.append(('args', obj.params()))
        meta.append(('source', str_to_bytes(obj.body())))

        # Proxy roles

        proxy_roles = []
        for role in obj.valid_roles():
            if obj.manage_haveProxy(role):
                proxy_roles.append(role)
        proxy_roles.sort()
        meta.append(('proxy_roles', proxy_roles))

        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.ZPythonScript_setTitle(title=d['title'])
        obj.ZPythonScript_edit(params=d['args'],
                               body=bytes_to_str(d['source']))
        obj.ZBindings_edit(mapping=dict(d['bindings']))
        obj.manage_proxy(roles=d['proxy_roles'])
        return


class ZCacheableObj(ModObj):
    meta_types = ['ZCacheable', ]

    def implements(self, obj):
        return hasattr(obj, 'ZCacheable_getManagerId')

    def read(self, obj):
        meta = []
        zcachemanager = obj.ZCacheable_getManagerId()
        if zcachemanager:
            meta.append(('zcachemanager', zcachemanager))

        return meta

    def write(self, obj, data):
        d = dict(data)
        zcachemanager = d.get('zcachemanager', '')
        obj.ZCacheable_setManagerId(zcachemanager)
        return


class ZPsycopgDAObj(ModObj):
    meta_types = ['Z Psycopg 2 Database Connection',
                  'Z Psycopg Database Connection', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZPsycopgDA'].manage_addZPsycopgConnection(
            id=obj_id,
            title=d['title'],
            connection_string=d['connection_string'],
        )
        return

    def read(self, obj):
        meta = []
        # late additions may not yet be everywhere in the Data.fs
        try:
            autocommit = obj.autocommit
        except AttributeError:
            autocommit = False
        try:
            readonlymode = obj.readonlymode
        except AttributeError:
            readonlymode = False

        meta.append(('autocommit', autocommit))
        meta.append(('connection_string', obj.connection_string))
        meta.append(('encoding', obj.encoding))
        meta.append(('readonlymode', readonlymode))
        meta.append(('tilevel', obj.tilevel))
        meta.append(('zdatetime', obj.zdatetime))
        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.manage_edit(
            title=d['title'],
            connection_string=d['connection_string'],
            zdatetime=d['zdatetime'],
            tilevel=d['tilevel'],
            autocommit=d['autocommit'],
            readonlymode=d['readonlymode'],
            encoding=d['encoding'],
        )
        return


class ZPyODBCDAObj(ModObj):
    meta_types = ['Z PyODBC Database Connection', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZPyODBCDA'].addpyodbcConnectionBrowser(
            id=obj_id,
            title=d['title'],
            connection_string=d['connection_string'],
            auto_commit=d['autocommit'],
            MaxRows=d['maxrows'],
        )
        return

    def read(self, obj):
        meta = []
        meta.append(('autocommit', obj.auto_commit))
        meta.append(('connection_string', obj.connx_string))
        meta.append(('maxrows', obj.MaxRows))
        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.manage_edit(
            title=d['title'],
            connection_string=d['connection_string'],
            auto_commit=d['autocommit'],
            MaxRows=d['maxrows'],
        )
        return


class ZcxOracleDAObj(ModObj):
    meta_types = ['Z cxOracle Database Connection', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZcxOracleDA'].manage_addZcxOracleConnection(
            id=obj_id,
            title=d['title'],
            connection_string=d['connection_string'],
        )
        return

    def read(self, obj):
        meta = []
        meta.append(('connection_string', obj.connection_string))
        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.manage_edit(
            title=d['title'],
            connection_string=d['connection_string'],
        )
        return


class ZsapdbDAObj(ModObj):
    meta_types = ['Z sap Database Connection', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['ZsapdbDA'].manage_addZsapdbConnection(
            id=obj_id,
            title=d['title'],
            connection_string=d['connection_string'],
        )
        return

    def read(self, obj):
        meta = []
        meta.append(('connection_string', obj.connection_string))
        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.manage_edit(
            title=d['title'],
            connection_string=d['connection_string'],
        )
        return


class SimpleUserFolderObj(FolderObj):
    meta_types = ['Simple User Folder', ]

    def create(self, obj, data, obj_id):
        obj.manage_addProduct['SimpleUserFolder'].addSimpleUserFolder()
        return


class MailHostObj(ModObj):
    meta_types = ['Mail Host', ]

    def create(self, obj, data, obj_id):
        d = dict(data)
        # id, title, connection_string, check, zdatetime, tilevel, autocommit,
        # encoding
        obj.manage_addProduct['MailHost'].manage_addMailHost(
            id=obj_id,
            title=d['title'],
            smtp_host=d['smtp_host'],
            smtp_port=d['smtp_port'],
        )
        return

    def read(self, obj):
        meta = []
        # "autocommit" is a late addition, which may not yet be everywhere in
        # the Data.fs. Default to False
        meta.append(('smtp_host', obj.smtp_host))
        meta.append(('smtp_port', obj.smtp_port))
        meta.append(('smtp_uid', obj.smtp_uid))
        meta.append(('smtp_pwd', obj.smtp_pwd))
        meta.append(('force_tls', obj.force_tls))
        meta.append(('smtp_queue', obj.smtp_queue))
        meta.append(('smtp_queue_directory', obj.smtp_queue_directory))
        return meta

    def write(self, obj, data):
        d = dict(data)
        obj.manage_makeChanges(
            title=d['title'],
            smtp_host=d['smtp_host'],
            smtp_port=d['smtp_port'],
            smtp_uid=d.get('smtp_uid', ''),
            smtp_pwd=d.get('smtp_pwd', ''),
            force_tls=d.get('force_tls', False),
            smtp_queue=d.get('smtp_queue', ''),
            smtp_queue_directory=d.get('smtp_queue_directory', ''),
        )
        return


object_types = {
    'AccessControl': AccessControlObj,
    'DTML Document': DTMLDocumentObj,
    'DTML Method': DTMLMethodObj,
    'DTML TeX': DTMLTeXObj,
    'External Method': ExternalMethodObj,
    'File': FileObj,
    'Folder': FolderObj,
    'Folder (Ordered)': FolderOrderedObj,
    'Image': ImageObj,
    'Page Template': PageTemplateObj,
    'Properties': PropertiesObj,
    'RAM Cache Manager': RAMCacheManagerObj,
    'Accelerated HTTP Cache Manager': AcceleratedHTTPCacheManagerObj,
    'Script (Python)': ScriptPythonObj,
    'Z SQL Method': ZSQLMethodObj,
    'ZCacheable': ZCacheableObj,
    'Z Psycopg 2 Database Connection': ZPsycopgDAObj,
    'Z Psycopg Database Connection': ZPsycopgDAObj,
    'Z PyODBC Database Connection': ZPyODBCDAObj,
    'Z cxOracle Database Connection': ZcxOracleDAObj,
    'Z sapdb Database Connection': ZsapdbDAObj,
    'Simple User Folder': SimpleUserFolderObj,
    'Mail Host': MailHostObj,
    'ZForce': ZForceObj,
    'User Folder': UserFolderObj,
}

object_handlers = {
    key: value() for key, value in object_types.items()
}

def mod_implemented_handlers(obj, meta_type):
    known_types = list(object_handlers.keys())
    interfaces = ['Properties', 'AccessControl', 'ZCacheable', ]
    interfaces.append(meta_type)
    # return all object handlers for interfaces the object implements
    handlers = [object_handlers[i] for i in interfaces]
    return [h for h in handlers if h.implements(obj)]

