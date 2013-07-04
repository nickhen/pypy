
from pypy.interpreter.baseobjspace import W_Root
from rpython.tool.pairtype import extendabletype
from pypy.module.micronumpy.support import calc_strides
from pypy.module.micronumpy.arrayimpl.base import BaseArrayImplementation


def issequence_w(space, w_obj):
    return (space.isinstance_w(w_obj, space.w_tuple) or
            space.isinstance_w(w_obj, space.w_list) or
            isinstance(w_obj, W_NDimArray))

def wrap_impl(space, cls, impl):
    if space.is_w(space.type(cls), space.gettypefor(W_NDimArray)):
        ret = W_NDimArray(impl)
    else:
        ret = space.allocate_instance(W_NDimArray, space.type(cls))
        print 'created',space.type(ret)
        W_NDimArray.__init__(ret, impl)
    return ret

class ArrayArgumentException(Exception):
    pass


class W_NDimArray(W_Root):
    __metaclass__ = extendabletype

    def __init__(self, implementation):
        assert isinstance(implementation, BaseArrayImplementation)
        self.implementation = implementation

    @staticmethod
    def from_shape(space, shape, dtype, order='C', subtype=None):
        from pypy.module.micronumpy.arrayimpl import concrete, scalar

        if not shape:
            impl = scalar.Scalar(dtype.base)
        else:
            strides, backstrides = calc_strides(shape, dtype.base, order)
            impl = concrete.ConcreteArray(shape, dtype.base, order, strides,
                                      backstrides)
        if subtype:
            ret = space.allocate_instance(W_NDimArray, subtype)
            W_NDimArray.__init__(ret, impl)
            return ret
        return W_NDimArray(impl)

    @staticmethod
    def from_shape_and_storage(space, shape, storage, dtype, order='C', owning=False, subtype=None):
        from pypy.module.micronumpy.arrayimpl import concrete
        assert shape
        strides, backstrides = calc_strides(shape, dtype, order)
        if owning:
            # Will free storage when GCd
            impl = concrete.ConcreteArray(shape, dtype, order, strides,
                                                backstrides, storage=storage)
        else:
            impl = concrete.ConcreteArrayNotOwning(shape, dtype, order, strides,
                                                backstrides, storage)
        if subtype:
            ret = space.allocate_instance(W_NDimArray, subtype)
            W_NDimArray.__init__(ret, impl)
            return ret
        return W_NDimArray(impl)

    @staticmethod
    def new_slice(space, offset, strides, backstrides, shape, parent, orig_arr, dtype=None):
        from pypy.module.micronumpy.arrayimpl import concrete

        impl = concrete.SliceArray(offset, strides, backstrides, shape, parent,
                                   orig_arr, dtype)
        return wrap_impl(space, orig_arr, impl)

    @staticmethod
    def new_scalar(space, dtype, w_val=None):
        from pypy.module.micronumpy.arrayimpl import scalar

        if w_val is not None:
            w_val = dtype.coerce(space, w_val)
        return W_NDimArray(scalar.Scalar(dtype, w_val))


def convert_to_array(space, w_obj):
    from pypy.module.micronumpy.interp_numarray import array
    from pypy.module.micronumpy import interp_ufuncs

    if isinstance(w_obj, W_NDimArray):
        return w_obj
    elif issequence_w(space, w_obj):
        # Convert to array.
        return array(space, w_obj, w_order=None)
    else:
        # If it's a scalar
        dtype = interp_ufuncs.find_dtype_for_scalar(space, w_obj)
        return W_NDimArray.new_scalar(space, dtype, w_obj)
