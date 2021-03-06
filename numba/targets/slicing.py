"""
Implement slices and various slice computations.
"""

from llvmlite import ir

from numba import cgutils, types, typing
from .imputils import (builtin, builtin_attr, implement,
                       impl_attribute, impl_attribute_generic,
                       iternext_impl, struct_factory,
                       impl_ret_borrowed, impl_ret_new_ref,
                       impl_ret_untracked)


def fix_index(builder, idx, size):
    """
    Fix negative index by adding *size* to it.  Positive
    indices are left untouched.
    """
    is_negative = builder.icmp_signed('<', idx, ir.Constant(size.type, 0))
    wrapped_index = builder.add(idx, size)
    return builder.select(is_negative, wrapped_index, idx)


def fix_slice(builder, slice, size):
    """
    Fix *slice* start and stop to be valid (inclusive and exclusive, resp)
    indexing bounds for a sequence of the given *size*.
    """
    # See PySlice_GetIndicesEx()
    zero = ir.Constant(size.type, 0)
    minus_one = ir.Constant(size.type, -1)

    def fix_bound(bound_name, lower_repl, upper_repl):
        bound = getattr(slice, bound_name)
        bound = fix_index(builder, bound, size)
        # Store value
        setattr(slice, bound_name, bound)
        # Still negative? => clamp to lower_repl
        underflow = builder.icmp_signed('<', bound, zero)
        with builder.if_then(underflow, likely=False):
            setattr(slice, bound_name, lower_repl)
        # Greater than size? => clamp to upper_repl
        overflow = builder.icmp_signed('>=', bound, size)
        with builder.if_then(overflow, likely=False):
            setattr(slice, bound_name, upper_repl)

    with builder.if_else(cgutils.is_neg_int(builder, slice.step)) as (if_neg_step, if_pos_step):
        with if_pos_step:
            # < 0 => 0; >= size => size
            fix_bound('start', zero, size)
            fix_bound('stop', zero, size)
        with if_neg_step:
            # < 0 => -1; >= size => size - 1
            lower = minus_one
            upper = builder.add(size, minus_one)
            fix_bound('start', lower, upper)
            fix_bound('stop', lower, upper)


def get_slice_length(builder, slicestruct):
    """
    Given a slice, compute the number of indices it spans, i.e. the
    number of iterations that for_range_slice() will execute.

    Pseudo-code:
        assert step != 0
        if step > 0:
            if stop <= start:
                return 0
            else:
                return (stop - start - 1) // step + 1
        else:
            if stop >= start:
                return 0
            else:
                return (stop - start + 1) // step + 1

    (see PySlice_GetIndicesEx() in CPython)
    """
    start = slicestruct.start
    stop = slicestruct.stop
    step = slicestruct.step
    one = ir.Constant(start.type, 1)
    zero = ir.Constant(start.type, 0)

    is_step_negative = cgutils.is_neg_int(builder, step)
    delta = builder.sub(stop, start)

    # Nominal case
    pos_dividend = builder.sub(delta, one)
    neg_dividend = builder.add(delta, one)
    dividend  = builder.select(is_step_negative, neg_dividend, pos_dividend)
    nominal_length = builder.add(one, builder.sdiv(dividend, step))

    # Catch zero length
    is_zero_length = builder.select(is_step_negative,
                                    builder.icmp_signed('>=', delta, zero),
                                    builder.icmp_signed('<=', delta, zero))

    # Clamp to 0 if is_zero_length
    return builder.select(is_zero_length, zero, nominal_length)


def fix_stride(builder, slice, stride):
    """
    Fix the given stride for the slice's step.
    """
    return builder.mul(slice.step, stride)


#---------------------------------------------------------------------------
# The slice structure

class Slice(cgutils.Structure):
    _fields = [('start', types.intp),
               ('stop', types.intp),
               ('step', types.intp), ]


@builtin
@implement(types.slice_type, types.intp, types.intp, types.intp)
def slice3_impl(context, builder, sig, args):
    start, stop, step = args

    slice3 = Slice(context, builder)
    slice3.start = start
    slice3.stop = stop
    slice3.step = step

    res = slice3._getvalue()
    return impl_ret_untracked(context, builder, sig.return_type, res)


@builtin
@implement(types.slice_type, types.intp, types.intp)
def slice2_impl(context, builder, sig, args):
    start, stop = args

    slice3 = Slice(context, builder)
    slice3.start = start
    slice3.stop = stop
    slice3.step = context.get_constant(types.intp, 1)

    res = slice3._getvalue()
    return impl_ret_untracked(context, builder, sig.return_type, res)


@builtin
@implement(types.slice_type, types.intp, types.none)
def slice1_start_impl(context, builder, sig, args):
    start, stop = args

    slice3 = Slice(context, builder)
    slice3.start = start
    maxint = (1 << (context.address_size - 1)) - 1
    slice3.stop = context.get_constant(types.intp, maxint)
    slice3.step = context.get_constant(types.intp, 1)

    res = slice3._getvalue()
    return impl_ret_untracked(context, builder, sig.return_type, res)


@builtin
@implement(types.slice_type, types.none, types.intp)
def slice1_stop_impl(context, builder, sig, args):
    start, stop = args

    slice3 = Slice(context, builder)
    slice3.start = context.get_constant(types.intp, 0)
    slice3.stop = stop
    slice3.step = context.get_constant(types.intp, 1)

    res = slice3._getvalue()
    return impl_ret_untracked(context, builder, sig.return_type, res)


@builtin
@implement(types.slice_type)
def slice0_empty_impl(context, builder, sig, args):
    assert not args

    slice3 = Slice(context, builder)
    slice3.start = context.get_constant(types.intp, 0)
    maxint = (1 << (context.address_size - 1)) - 1
    slice3.stop = context.get_constant(types.intp, maxint)
    slice3.step = context.get_constant(types.intp, 1)

    res = slice3._getvalue()
    return impl_ret_untracked(context, builder, sig.return_type, res)


@builtin
@implement(types.slice_type, types.none, types.none)
def slice0_none_none_impl(context, builder, sig, args):
    assert len(args) == 2
    newsig = typing.signature(types.slice_type)
    return slice0_empty_impl(context, builder, newsig, ())
