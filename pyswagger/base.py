from __future__ import absolute_import
import six
import weakref


class ContainerType:
    """ Enum of Container-Types
    """

    # list container
    list_ = 1

    # dict container
    dict_ = 2

    # dict of list container, like: {'xx': [], 'xx': [], ...}
    dict_of_list_ = 3


class Context(object):
    """ Base of all parsing contexts """

    # required fields, a list of strings
    __swagger_required__ = []
    
    # parsing context of children fields,
    # a list of tuple (field-name, container-type, parsing-context)
    __swagger_child__ = []

    # factory of object to be created according to
    # this parsing context.
    __swagger_ref_obj__ = None

    def __init__(self, parent_obj, backref):
        """
        constructor

        :param dict parent_obj: parent object placeholder
        :param str backref: the key to parent object placeholder
        """

        # object placeholder of parent object
        self._parent_obj = parent_obj

        # key used to indicate the location in
        # parent object when parsing finished.
        self._backref = backref

        self.__reset_obj()

    def __enter__(self):
        return self

    def __reset_obj(self):
        self._obj = {}

    def __exit__(self, exc_type, exc_value, traceback):
        """ When exiting parsing context, doing two things
        - create the object corresponding to this parsing context.
        - populate the created object to parent context.
        """
        if self._obj == None:
            return

        obj = self.__class__.__swagger_ref_object__(self)
        self.__reset_obj()

        if isinstance(self._parent_obj[self._backref], list):
            self._parent_obj[self._backref].append(obj)
        else:
            self._parent_obj[self._backref] = obj

    def parse(self, obj=None):
        """ major part do parsing.

        :param dict obj: json object to be parsed.
        :raises ValueError: if obj is not a dict type.
        """
        if obj == None:
            return

        if not isinstance(obj, dict):
            raise ValueError('invalid obj passed: ' + str(type(obj)))

        if hasattr(self, '__swagger_child__'):
            # to nested objects
            for key, ct, ctx_kls in self.__swagger_child__:
                items = obj.get(key, None)

                # make all containers to something not None
                if ct == ContainerType.list_:
                    self._obj[key] = []
                elif ct:
                    self._obj[key] = {}

                if items == None:
                    continue

                # deep into children
                if ct == None:
                    self._obj[key] = {}
                    with ctx_kls(self._obj, key) as ctx:
                        ctx.parse(obj=items)
                elif ct == ContainerType.list_:
                    for item in items:
                        with ctx_kls(self._obj, key) as ctx:
                            ctx.parse(obj=item)
                elif ct == ContainerType.dict_:
                    for k, v in six.iteritems(items):
                        self._obj[key][k] = {}
                        with ctx_kls(self._obj[key], k) as ctx:
                            ctx.parse(obj=v)
                elif ct == ContainerType.dict_of_list_:
                    for k, v in six.iteritems(items):
                        self._obj[key][k] = []
                        for vv in v:
                            with ctx_kls(self._obj[key], k) as ctx:
                                ctx.parse(obj=vv)

        # update _obj with obj
        if self._obj != None:
            for key in (set(obj.keys()) - set(self._obj.keys())):
                self._obj[key] = obj[key]
        else:
            self._obj = obj


class BaseObj(object):
    """ Base implementation of all referencial objects,
    """

    # fields that need re-named.
    __swagger_rename__ = {}

    # list of names of fields, we will skip fields not in this list.
    __swagger_fields__ = []

    def __init__(self, ctx):
        """ constructor

        :param Context ctx: parsing context used to create this object
        :raises TypeError: if ctx is not a subclass of Context.
        """
        super(BaseObj, self).__init__()

        # init parent reference
        self._parent__ = None

        if not issubclass(type(ctx), Context):
            raise TypeError('should provide args[0] as Context, not: ' + ctx.__class__.__name__)

        # handle fields
        for field in self.__swagger_fields__:
            self.update_field(field, ctx._obj.get(field, None))

        # set self as childrent's parent
        for name, _, cls in ctx.__swagger_child__:
            obj = getattr(self, name)

            def assign_parent(obj, cls, parent):
                if isinstance(obj, list):
                    for v in obj:
                        assign_parent(v, cls, parent)
                elif isinstance(obj, cls.__swagger_ref_object__):
                    obj._parent__ = parent

            if isinstance(obj, dict):
                # Objects from NamedContext
                for v in obj.values():
                    assign_parent(v, cls, self)
            else:
                assign_parent(obj, cls, self)


    def get_private_name(self, f):
        """ get private protected name of an attribute

        :param str f: name of the private attribute to be accessed.
        """
        f = self.__swagger_rename__[f] if f in self.__swagger_rename__.keys() else f
        return '_' + self.__class__.__name__ + '__' + f
 
    def update_field(self, f, obj):
        """ update a field

        :param str f: name of field to be updated.
        :param obj: value of field to be updated.
        """
        setattr(self, self.get_private_name(f), obj)

    @property
    def _parent_(self):
        """ get parent object

        :return: the parent object.
        :rtype: a subclass of BaseObj.
        """
        return self._parent__

    @property
    def _field_names_(self):
        """ get list of field names, will go through MRO
        to merge all fields in parent classes.

        :return: a list of field names
        :rtype: a list of str
        """
        ret = []

        def _merge(f, rename):
            if not f:
                return

            if not rename:
                ret.extend(f)
            else:
                for n in f:
                    new_n = rename.get(n, None)
                    ret.append(new_n) if new_n else ret.append(n)

        for b in self.__class__.__mro__:
            _merge(
                getattr(b, '__swagger_fields__', None),
                getattr(b, '__swagger_rename__', None)
            )

        return ret

    @property
    def _children_(self):
        """ get children objects

        :rtype: a list of tuples(name, child_object)
        """
        ret = []
        names = self._field_names_

        def down(name, obj):
            if isinstance(obj, BaseObj):
                if not isinstance(obj, weakref.ProxyTypes):
                    ret.append((name, obj))
            elif isinstance(obj, list):
                for v in obj:
                    down(name, v)
            elif isinstance(obj, dict):
                for k, v in six.iteritems(obj):
                    down(k, v)

        for n in names:
            down(None, getattr(self, n))

        return ret


def _method_(name):
    """ getter factory """
    def _getter_(self):
        return getattr(self, self.get_private_name(name))
    return _getter_


class FieldMeta(type):
    """ metaclass to init fields
    """
    def __new__(metacls, name, bases, spc):
        """ scan through MRO to get a merged list of fields,
        and create those fields.
        """
        def init_fields(fields, rename):
            for f in fields:
                f = rename[f] if f in rename.keys() else f
                spc[f] = property(_method_(f))

        rename = spc['__swagger_rename__'] if '__swagger_rename__' in spc.keys() else {}
        if '__swagger_fields__' in spc.keys():
            init_fields(spc['__swagger_fields__'], rename)

        for b in bases:
            fields = b.__swagger_fields__ if hasattr(b, '__swagger_fields__') else {}
            rename = b.__swagger_rename__ if hasattr(b, '__swagger_rename__') else {}
            init_fields(fields, rename)

        return type.__new__(metacls, name, bases, spc)

