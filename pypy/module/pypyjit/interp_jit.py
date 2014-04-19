"""This is not the JIT :-)

This is transformed to become a JIT by code elsewhere: rpython/jit/*
"""

from rpython.rlib.rarithmetic import r_uint, intmask
from rpython.rlib.jit import JitDriver, hint, we_are_jitted, dont_look_inside
from rpython.rlib import jit, rstm
from rpython.rlib.jit import current_trace_length, unroll_parameters
import pypy.interpreter.pyopcode   # for side-effects
from pypy.interpreter.error import OperationError, oefmt
from pypy.interpreter.pycode import CO_GENERATOR
from pypy.interpreter.pyframe import PyFrame
from pypy.interpreter.pyopcode import ExitFrame, Yield
from pypy.interpreter.pyopcode import PyPyJitDriver
from opcode import opmap


PyFrame._virtualizable_ = ['last_instr', 'pycode',
                           'valuestackdepth', 'locals_stack_w[*]',
                           'cells[*]',
                           'last_exception',
                           'lastblock',
                           'is_being_profiled',
                           'w_globals',
                           'w_f_trace',
                           ]

JUMP_ABSOLUTE = opmap['JUMP_ABSOLUTE']

def get_printable_location(next_instr, is_being_profiled, bytecode):
    from pypy.tool.stdlib_opcode import opcode_method_names
    name = opcode_method_names[ord(bytecode.co_code[next_instr])]
    return '%s #%d %s' % (bytecode.get_repr(), next_instr, name)


def should_unroll_one_iteration(next_instr, is_being_profiled, bytecode):
    return (bytecode.co_flags & CO_GENERATOR) != 0

pypyjitdriver = PyPyJitDriver(get_printable_location = get_printable_location,
                              should_unroll_one_iteration =
                              should_unroll_one_iteration,
                              name='pypyjit')

class __extend__(PyFrame):

    def dispatch(self, pycode, next_instr, ec):
        self = hint(self, access_directly=True)
        next_instr = r_uint(next_instr)
        is_being_profiled = self.is_being_profiled
        try:
            while True:
                pypyjitdriver.jit_merge_point(ec=ec,
                    frame=self, next_instr=next_instr, pycode=pycode,
                    is_being_profiled=is_being_profiled)
                # nothing inbetween!
                if rstm.jit_stm_should_break_transaction(False):
                    rstm.jit_stm_transaction_break_point()
                co_code = pycode.co_code
                self.valuestackdepth = hint(self.valuestackdepth, promote=True)
                next_instr = self.handle_bytecode(co_code, next_instr, ec)
                is_being_profiled = self.is_being_profiled
        except Yield:
            self.last_exception = None
            w_result = self.popvalue()
            jit.hint(self, force_virtualizable=True)
            return w_result
        except ExitFrame:
            self.last_exception = None
            return self.popvalue()

    def jump_absolute(self, jumpto, ec):
        if we_are_jitted():
            #
            # assume that only threads are using the bytecode counter
            decr_by = 0
            if self.space.actionflag.has_bytecode_counter:   # constant-folded
                if self.space.threadlocals.gil_ready:   # quasi-immutable field
                    decr_by = _get_adapted_tick_counter()
            #
            self.last_instr = intmask(jumpto)
            ec.bytecode_trace(self, decr_by)
            jumpto = r_uint(self.last_instr)
            if rstm.jit_stm_should_break_transaction(True):
                rstm.jit_stm_transaction_break_point()
        #
        pypyjitdriver.can_enter_jit(frame=self, ec=ec, next_instr=jumpto,
                                    pycode=self.getcode(),
                                    is_being_profiled=self.is_being_profiled)
        return jumpto

def _get_adapted_tick_counter():
    # Normally, the tick counter is decremented by 100 for every
    # Python opcode.  Here, to better support JIT compilation of
    # small loops, we decrement it by a possibly smaller constant.
    # We get the maximum 100 when the (unoptimized) trace length
    # is at least 3200 (a bit randomly).
    trace_length = r_uint(current_trace_length())
    decr_by = trace_length // 32
    if decr_by < 1:
        decr_by = 1
    elif decr_by > 100:    # also if current_trace_length() returned -1
        decr_by = 100
    return intmask(decr_by)


# ____________________________________________________________
#
# Public interface

def set_param(space, __args__):
    '''Configure the tunable JIT parameters.
        * set_param(name=value, ...)            # as keyword arguments
        * set_param("name=value,name=value")    # as a user-supplied string
        * set_param("off")                      # disable the jit
        * set_param("default")                  # restore all defaults
    '''
    # XXXXXXXXX
    args_w, kwds_w = __args__.unpack()
    if len(args_w) > 1:
        raise oefmt(space.w_TypeError,
                    "set_param() takes at most 1 non-keyword argument, %d "
                    "given", len(args_w))
    if len(args_w) == 1:
        text = space.str_w(args_w[0])
        try:
            jit.set_user_param(None, text)
        except ValueError:
            raise OperationError(space.w_ValueError,
                                 space.wrap("error in JIT parameters string"))
    for key, w_value in kwds_w.items():
        if key == 'enable_opts':
            jit.set_param(None, 'enable_opts', space.str_w(w_value))
        else:
            intval = space.int_w(w_value)
            for name, _ in unroll_parameters:
                if name == key and name != 'enable_opts':
                    jit.set_param(None, name, intval)
                    break
            else:
                raise oefmt(space.w_TypeError, "no JIT parameter '%s'", key)

@dont_look_inside
def residual_call(space, w_callable, __args__):
    '''For testing.  Invokes callable(...), but without letting
    the JIT follow the call.'''
    return space.call_args(w_callable, __args__)
