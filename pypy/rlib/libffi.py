from pypy.rpython.lltypesystem import rffi, lltype
from pypy.rlib.objectmodel import specialize
from pypy.rlib.rarithmetic import intmask, r_uint
from pypy.rlib import jit
from pypy.rlib import clibffi
from pypy.rlib.clibffi import get_libc_name, FUNCFLAG_CDECL, AbstractFuncPtr, \
    push_arg_as_ffiptr, c_ffi_call
from pypy.rlib.rdynload import dlopen, dlclose, dlsym, dlsym_byordinal

def import_types():
    g = globals()
    for key, value in clibffi.__dict__.iteritems():
        if key.startswith('ffi_type_'):
            g[key] = value
import_types()
del import_types


# ----------------------------------------------------------------------

class AbstractArg(object):
    next = None

class IntArg(AbstractArg):
    """ An argument holding an integer
    """

    def __init__(self, intval):
        self.intval = intval

    def push(self, func, ll_args, i):
        func._push_arg(self.intval, ll_args, i)

class FloatArg(AbstractArg):
    """ An argument holding a float
    """

    def __init__(self, floatval):
        self.floatval = floatval

    def push(self, func, ll_args, i):
        func._push_arg(self.floatval, ll_args, i)


class Func(AbstractFuncPtr):

    _immutable_fields_ = ['funcsym', 'argtypes', 'restype']

    def __init__(self, name, argtypes, restype, funcsym, flags=FUNCFLAG_CDECL,
                 keepalive=None):
        AbstractFuncPtr.__init__(self, name, argtypes, restype, flags)
        self.keepalive = keepalive
        self.funcsym = funcsym

    def _prepare(self):
        ll_args = lltype.malloc(rffi.VOIDPP.TO, len(self.argtypes), flavor='raw')
        return ll_args
    _prepare.oopspec = 'libffi_prepare_call(self)'

    def _push_arg(self, value, ll_args, i):
        # XXX: check the type is not translated?
        argtype = self.argtypes[i]
        c_size = intmask(argtype.c_size)
        ll_buf = lltype.malloc(rffi.CCHARP.TO, c_size, flavor='raw')
        push_arg_as_ffiptr(argtype, value, ll_buf)
        ll_args[i] = ll_buf
    # XXX this is bad, fix it somehow in the future, but specialize:argtype
    # doesn't work correctly with mixing non-negative and normal integers
    #_push_arg._annenforceargs_ = [None, int]
    _push_arg._annspecialcase_ = 'specialize:argtype(1)'
    _push_arg.oopspec = 'libffi_push_arg(self, value, ll_args, i)'

    def _do_call(self, funcsym, ll_args, RESULT):
        # XXX: check len(args)?
        ll_result = lltype.nullptr(rffi.CCHARP.TO)
        if self.restype != ffi_type_void:
            ll_result = lltype.malloc(rffi.CCHARP.TO,
                                      intmask(self.restype.c_size),
                                      flavor='raw')
        ffires = c_ffi_call(self.ll_cif,
                            self.funcsym,
                            rffi.cast(rffi.VOIDP, ll_result),
                            rffi.cast(rffi.VOIDPP, ll_args))
        if RESULT is not lltype.Void:
            TP = lltype.Ptr(rffi.CArray(RESULT))
            res = rffi.cast(TP, ll_result)[0]
        else:
            res = None
        self._free_buffers(ll_result, ll_args)
        #check_fficall_result(ffires, self.flags)
        return res
    _do_call._annspecialcase_ = 'specialize:arg(2)'
    _do_call.oopspec = 'libffi_call(self, funcsym, RESULT)'

    def _free_buffers(self, ll_result, ll_args):
        lltype.free(ll_result, flavor='raw')
        for i in range(len(self.argtypes)):
            lltype.free(ll_args[i], flavor='raw')
        lltype.free(ll_args, flavor='raw')

    @jit.unroll_safe
    @specialize.arg(2)
    def call(self, argchain, RESULT):
        # WARNING!  This code is written carefully in a way that the JIT
        # optimizer will see a sequence of calls like the following:
        #
        #    libffi_prepare_call
        #    libffi_push_arg
        #    libffi_push_arg
        #    ...
        #    libffi_call
        #
        # It is important that there is no other operation in the middle, else
        # the optimizer will fail to recognize the pattern and won't turn it
        # into a fast CALL.  Note that "arg = arg.next" is optimized away,
        # assuming that archain is completely virtual.
        ll_args = self._prepare()
        i = 0
        arg = argchain
        while arg:
            arg.push(self, ll_args, i)
            i += 1
            arg = arg.next
        result = self._do_call(self.funcsym, ll_args, RESULT)
        return result

# ----------------------------------------------------------------------
    

# XXX: it partially duplicate the code in clibffi.py
class CDLL(object):
    def __init__(self, libname):
        """Load the library, or raises DLOpenError."""
        self.lib = lltype.nullptr(rffi.CCHARP.TO)
        ll_libname = rffi.str2charp(libname)
        try:
            self.lib = dlopen(ll_libname)
        finally:
            lltype.free(ll_libname, flavor='raw')

    def __del__(self):
        if self.lib:
            dlclose(self.lib)
            self.lib = lltype.nullptr(rffi.CCHARP.TO)

    def getpointer(self, name, argtypes, restype, flags=FUNCFLAG_CDECL):
        return Func(name, argtypes, restype, dlsym(self.lib, name),
                    flags=flags, keepalive=self)
