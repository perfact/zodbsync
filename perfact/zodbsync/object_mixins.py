import AccessControl.Permission
import zExceptions

"""
Handlers for reading and writing object information that can be implemented in
addition to meta_type specific information
"""


class MixinModObj(object):
    @staticmethod
    def read(obj):  # pragma: no cover
        """
        Read an object and return a dictionary
        """
        raise NotImplementedError

    @staticmethod
    def write(obj, data):  # pragma: no cover
        """
        Write data to an existing object
        """
        raise NotImplementedError

    @staticmethod
    def implements(obj):  # pragma: no cover
        """
        Decide if this handler can be applied to the object in question
        """
        raise NotImplementedError


class AccessControlObj(MixinModObj):
    @staticmethod
    def roles(obj):
        """Read currently set userdefined roles"""
        return tuple(sorted(obj.userdefined_roles()))

    @staticmethod
    def local_roles(obj):
        """Read currently set local roles"""
        # Ignore local owner role if it is trivial
        return list(sorted([
            role for role in obj.get_local_roles()
            if role[1] != ('Owner',)]
        ))

    @staticmethod
    def implements(obj):
        return True

    @staticmethod
    def read(obj):
        result = {}

        is_root = obj.isTopLevelPrincipiaApplicationObject
        if is_root:
            result['is_root'] = True

        roles = AccessControlObj.roles(obj)
        if roles:
            result['roles'] = roles

        local_roles = AccessControlObj.local_roles(obj)
        if local_roles:
            result['local_roles'] = local_roles

        try:
            result['owner'] = obj._owner
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

        perms = []
        for perm in perm_set:
            roles = perm.getRoles(default=[])
            # for some reason, someone decided to encode whether a permission
            # is acquired by returning either a tuple or a list...
            acquire = isinstance(roles, list)
            roles = list(roles)
            roles.sort()
            if acquire and not len(roles) or is_root and roles == ['Manager']:
                # Does not deviate from default
                continue
            perms.append((perm.name, acquire, roles))

        if perms:
            perms.sort()
            result['perms'] = perms

        return result

    @staticmethod
    def write(obj, data):

        # Set userdef roles
        cur = AccessControlObj.roles(obj)
        tgt = data.get('roles', [])
        for role in tgt:
            if role not in cur:
                obj._addRole(role)
        todelete = [r for r in cur if r not in tgt]
        if todelete:
            obj._delRoles(todelete)

        # Set local roles
        cur = dict(AccessControlObj.local_roles(obj))
        tgt = dict(data.get('local_roles', tuple()))
        users = set(cur.keys()) | set(tgt.keys())
        for user in users:
            if user not in tgt:
                obj.manage_delLocalRoles([user])
            elif cur.get(user) != tgt[user]:
                obj.manage_setLocalRoles(user, tgt[user])

        # Permission settings
        # permissions that are not stored are understood to be acquired, with
        # no additional roles being granted this permission
        # An exception is the root application object, which can not acquire
        stored_perms = {
            name: (acquire, roles)
            for name, acquire, roles in data.get('perms', [])
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
                # allow Manager
                if obj.isTopLevelPrincipiaApplicationObject:
                    roles = ('Manager',)
                else:
                    roles = []
            AccessControl.Permission.Permission(name, [], obj).setRoles(roles)

        # set ownership
        if 'owner' in data:
            owner = data['owner']
            if isinstance(owner, str):
                # backward compatibility for older behavior, where the
                # corresponding UserFolder was not included
                owner = (['acl_users'], owner)

            obj._owner = data['owner']


class PropertiesObj(MixinModObj):
    @staticmethod
    def implements(obj):
        if hasattr(obj, 'aq_explicit'):
            me = obj.aq_explicit
        else:
            me = obj
        return hasattr(me, 'propertyMap')

    @staticmethod
    def read(obj):
        props = obj.propertyMap()

        # Optional: Ignore the "title" property if it exists
        props = list([a for a in props if a['id'] != 'title'])

        for prop in props:
            prop['value'] = obj.getProperty(prop['id'])
            # Handle inherited properties correctly
            if 'mode' in prop:
                val = getattr(obj, prop['id'])
                del prop['mode']
                prop['value'] = val

        props = [sorted(a.items()) for a in props]

        # Sort the properties
        props.sort()

        if props:
            return {'props': props}

        return {}

    @staticmethod
    def write(obj, data):
        props = [dict(prop) for prop in data.get('props', [])]
        ids = {prop['id'] for prop in props}
        vals = {prop['id']: prop['value'] for prop in props}
        types = {prop['id']: prop['type'] for prop in props}
        cur = obj.propertyIds()

        # Delete any property that is superfluous or has the wrong type
        del_ids = [
            p for p in cur
            if p != 'title'
            and (p not in ids or types[p] != obj.getPropertyType(p))
        ]
        for p in del_ids:
            try:
                obj.manage_delProperties(ids=[p])
            except zExceptions.BadRequest as e:
                if str(e) == 'Cannot delete output_encoding':
                    print("Ignoring failed attempt to delete output_encoding")
                else:
                    raise

        # Add any property that should exist but is missing
        for prop in ids:
            if prop not in cur or prop in del_ids:
                obj._setProperty(prop, vals[prop], types[prop])

        # Change properties that are not deleted and have the wrong value
        chg = {
            p: vals[p]
            for p in ids
            if p not in del_ids and p in cur and vals[p] != obj.getProperty(p)
        }
        if chg:
            obj.manage_changeProperties(**chg)


class ZCacheableObj(MixinModObj):
    @staticmethod
    def implements(obj):
        return hasattr(obj, 'ZCacheable_getManagerId')

    @staticmethod
    def read(obj):
        meta = {}
        zcachemanager = obj.ZCacheable_getManagerId()
        if zcachemanager:
            meta['zcachemanager'] = zcachemanager
        return meta

    @staticmethod
    def write(obj, data):
        zcachemanager = data.get('zcachemanager', '')
        obj.ZCacheable_setManagerId(zcachemanager)
        return
