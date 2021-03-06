from __future__ import print_function, absolute_import

from collections import namedtuple
import ctypes
import enum
import sys

import numpy as np

from numba import numpy_support, types, utils
from . import bufproto, cffi_utils


class Purpose(enum.Enum):
    # Value being typed is used as an argument
    argument = 1
    # Value being typed is used as a constant
    constant = 2


_TypeofContext = namedtuple("_TypeofContext", ("purpose",))

def typeof(val, purpose=Purpose.argument):
    """
    Get the Numba type of a Python value for the given purpose.
    """
    # Note the behaviour for Purpose.argument must match _typeof.c.
    c = _TypeofContext(purpose)
    return typeof_impl(val, c)


@utils.singledispatch
def typeof_impl(val, c):
    """
    Generic typeof() implementation.
    """
    tp = _typeof_buffer(val, c)
    if tp is not None:
        return tp

    # cffi is handled here as it does not expose a public base class
    # for exported functions.
    if cffi_utils.SUPPORTED and cffi_utils.is_cffi_func(val):
        return cffi_utils.make_function_type(val)

    return getattr(val, "_numba_type_", None)


def _typeof_buffer(val, c):
    if sys.version_info >= (2, 7):
        try:
            m = memoryview(val)
        except TypeError:
            return
        # Object has the buffer protocol
        try:
            dtype = bufproto.decode_pep3118_format(m.format, m.itemsize)
        except ValueError:
            return
        type_class = bufproto.get_type_class(type(val))
        layout = bufproto.infer_layout(m)
        return type_class(dtype, m.ndim, layout=layout,
                          readonly=m.readonly)


@typeof_impl.register(bool)
def _typeof_bool(val, c):
    return types.boolean

@typeof_impl.register(float)
def _typeof_bool(val, c):
    return types.float64

@typeof_impl.register(complex)
def _typeof_bool(val, c):
    return types.complex128

def _typeof_int(val, c):
    # As in _typeof.c
    nbits = utils.bit_length(val)
    if nbits < 32:
        typ = types.intp
    elif nbits < 64:
        typ = types.int64
    elif nbits == 64 and val >= 0:
        typ = types.uint64
    else:
        raise ValueError("Int value is too large: %s" % val)
    return typ

for cls in utils.INT_TYPES:
    typeof_impl.register(cls, _typeof_int)

@typeof_impl.register(np.generic)
def _typeof_numpy_scalar(val, c):
    try:
        return numpy_support.map_arrayscalar_type(val)
    except NotImplementedError:
        pass

@typeof_impl.register(str)
def _typeof_str(val, c):
    return types.string

@typeof_impl.register(type(None))
def _typeof_none(val, c):
    return types.none

@typeof_impl.register(tuple)
def _typeof_tuple(val, c):
    tys = [typeof_impl(v, c) for v in val]
    if any(ty is None for ty in tys):
        return
    return types.BaseTuple.from_types(tys, type(val))

@typeof_impl.register(np.dtype)
def _typeof_dtype(val, c):
    tp = numpy_support.from_dtype(val)
    return types.DType(tp)

@typeof_impl.register(np.ndarray)
def _typeof_ndarray(val, c):
    try:
        dtype = numpy_support.from_dtype(val.dtype)
    except NotImplementedError:
        return
    layout = numpy_support.map_layout(val)
    readonly = not val.flags.writeable
    return types.Array(dtype, val.ndim, layout, readonly=readonly)
