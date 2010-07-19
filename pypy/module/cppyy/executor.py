from pypy.rpython.lltypesystem import rffi, lltype

from pypy.module.cppyy import helper, capi


_executors = {}

class FunctionExecutor(object):
    def execute(self, space, func, cppthis, num_args, args):
        raise NotImplementedError("abstract base class")


class VoidExecutor(FunctionExecutor):
    def execute(self, space, func, cppthis, num_args, args):
        capi.c_call_v(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        return space.w_None

class BoolExecutor(FunctionExecutor):
    def execute(self, space, func, cppthis, num_args, args):
        result = capi.c_call_b(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        return space.wrap(result)

class CharExecutor(FunctionExecutor):
     def execute(self, space, func, cppthis, num_args, args):
        result = capi.c_call_c(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        return space.wrap(result)

class LongExecutor(FunctionExecutor):
    def execute(self, space, func, cppthis, num_args, args):
        result = capi.c_call_l(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        return space.wrap(result)

class DoubleExecutor(FunctionExecutor):
    def execute(self, space, func, cppthis, num_args, args):
        result = capi.c_call_d(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        return space.wrap(result)

class CStringExecutor(FunctionExecutor):
    def execute(self, space, func, cppthis, num_args, args):
        lresult = capi.c_call_l(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        ccpresult = rffi.cast(rffi.CCHARP, lresult)
        result = capi.charp2str_free(ccpresult)
        return space.wrap(result)


class InstancePtrExecutor(FunctionExecutor):
    _immutable_ = True
    def __init__(self, space, cpptype):
        self.cpptype = cpptype

    def execute(self, space, func, cppthis, num_args, args):
        from pypy.module.cppyy import interp_cppyy
        long_result = capi.c_call_l(func.cpptype.handle, func.method_index, cppthis, num_args, args)
        ptr_result = rffi.cast(rffi.VOIDP, long_result)
        return interp_cppyy.W_CPPInstance(self.cpptype, ptr_result)


def get_executor(space, name):
    from pypy.module.cppyy import interp_cppyy

    try:
        return _executors[name]
    except KeyError:
        pass

    compound = helper.compound(name)
    cpptype = interp_cppyy.type_byname(space, helper.clean_type(name))
    if compound == "*":           
        return InstancePtrExecutor(space, cpptype)

    return None # currently used until proper lazy instantiation available in interp_cppyy
 
 #  raise TypeError("no clue what %s is" % name)

_executors["void"]                = VoidExecutor()
_executors["bool"]                = BoolExecutor()
_executors["char"]                = CharExecutor()
_executors["unsigned char"]       = CharExecutor()
_executors["int"]                 = LongExecutor()
_executors["long int"]            = LongExecutor()
_executors["double"]              = DoubleExecutor()
_executors["char*"]               = CStringExecutor()
