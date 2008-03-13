from pypy.rpython.extregistry import ExtRegistryEntry
from pypy.rlib.objectmodel import CDefinedIntSymbolic

def purefunction(func):
    func._pure_function_ = True
    return func

def hint(x, **kwds):
    return x

class Entry(ExtRegistryEntry):
    _about_ = hint

    def compute_result_annotation(self, s_x, **kwds_s):
        from pypy.annotation import model as annmodel
        s_x = annmodel.not_const(s_x)
        if 's_access_directly' in kwds_s:
            if isinstance(s_x, annmodel.SomeInstance):
                from pypy.objspace.flow.model import Constant
                classdesc = s_x.classdef.classdesc
                virtualizable = classdesc.read_attribute('_virtualizable_',
                                                         Constant(False)).value
                if virtualizable:
                    flags = s_x.flags.copy()
                    flags['access_directly'] = True
                    s_x = annmodel.SomeInstance(s_x.classdef,
                                                s_x.can_be_None,
                                                flags)
        return s_x

    def specialize_call(self, hop, **kwds_i):
        from pypy.rpython.lltypesystem import lltype
        hints = {}
        for key, index in kwds_i.items():
            s_value = hop.args_s[index]
            if not s_value.is_constant():
                from pypy.rpython.error import TyperError
                raise TyperError("hint %r is not constant" % (key,))
            assert key.startswith('i_')
            hints[key[2:]] = s_value.const
        v = hop.inputarg(hop.args_r[0], arg=0)
        c_hint = hop.inputconst(lltype.Void, hints)
        hop.exception_cannot_occur()
        return hop.genop('hint', [v, c_hint], resulttype=v.concretetype)


def we_are_jitted():
    return False
# timeshifts to True

_we_are_jitted = CDefinedIntSymbolic('0 /* we are not jitted here */',
                                     default=0)

class Entry(ExtRegistryEntry):
    _about_ = we_are_jitted

    def compute_result_annotation(self):
        from pypy.annotation import model as annmodel
        return annmodel.SomeInteger(nonneg=True)

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        return hop.inputconst(lltype.Signed, _we_are_jitted)

def _is_early_constant(x):
    return False

class Entry(ExtRegistryEntry):
    _about_ = _is_early_constant

    def compute_result_annotation(self, s_value):
        from pypy.annotation import model as annmodel
        s = annmodel.SomeBool()
        if s_value.is_constant():
            s.const = True
        return s

    def specialize_call(self, hop):
        from pypy.rpython.lltypesystem import lltype
        if hop.s_result.is_constant():
            assert hop.s_result.const
            return hop.inputconst(lltype.Bool, True)
        v, = hop.inputargs(hop.args_r[0])
        return hop.genop('is_early_constant', [v], resulttype=lltype.Bool)

# ____________________________________________________________
# Internal

class _JitHintClassMethod(object):
    def __init__(self, name):
        self.name = name
    def __get__(self, instance, type):
        return _JitBoundClassMethod(type, self.name)

class _JitBoundClassMethod(object):
    def __init__(self, drivercls, name):
        self.drivercls = drivercls
        self.name = name
    def __eq__(self, other):
        return (isinstance(other, _JitBoundClassMethod) and
                self.drivercls is other.drivercls and
                self.name == other.name)
    def __ne__(self, other):
        return not (self == other)
    def __call__(self, **livevars):
        # ignore calls to the hint class methods when running on top of CPython
        pass

class Entry(ExtRegistryEntry):
    _type_ = _JitBoundClassMethod

    def compute_result_annotation(self, **kwds_s):
        from pypy.annotation import model as annmodel
        drivercls = self.instance.drivercls
        drivercls._check_class()
        keys = kwds_s.keys()
        keys.sort()
        expected = ['s_' + name for name in drivercls.greens + drivercls.reds]
        expected.sort()
        if keys != expected:
            raise JitHintError("%s.%s(): must give exactly the same keywords"
                               " as the 'greens' and 'reds'" % (
                drivercls.__name__, self.instance.name))
        return annmodel.s_None

    def specialize_call(self, hop, **kwds_i):
        # replace a call to MyDriverCls.hintname(**livevars)
        # with an operation 'hintname(MyDriverCls, livevars...)'
        # XXX to be complete, this could also check that the concretetype
        # of the variables are the same for each of the calls.
        from pypy.rpython.error import TyperError
        from pypy.rpython.lltypesystem import lltype
        drivercls = self.instance.drivercls
        greens_v = []
        reds_v = []
        for name in drivercls.greens:
            i = kwds_i['i_' + name]
            r_green = hop.args_r[i]
            v_green = hop.inputarg(r_green, arg=i)
            greens_v.append(v_green)
        for name in drivercls.reds:
            i = kwds_i['i_' + name]
            r_red = hop.args_r[i]
            v_red = hop.inputarg(r_red, arg=i)
            reds_v.append(v_red)
        hop.exception_cannot_occur()
        vlist = [hop.inputconst(lltype.Void, drivercls)]
        vlist.extend(greens_v)
        vlist.extend(reds_v)
        return hop.genop(self.instance.name, vlist,
                         resulttype=lltype.Void)

# ____________________________________________________________
# User interface for the hotpath JIT policy

class JitHintError(Exception):
    """Inconsistency in the JIT hints."""

class JitDriver:
    """Base class to declare fine-grained user control on the JIT process."""

    # NB. one of the points of requiring subclasses of this a class is
    # to support a single RPython program with several independent
    # JITting interpreters in it.  XXX that's not implemented yet.

    jit_merge_point = _JitHintClassMethod("jit_merge_point")
    can_enter_jit = _JitHintClassMethod("can_enter_jit")

    def _check_class(cls):
        if cls is JitDriver:
            raise JitHintError("must subclass JitDriver")
        for name in cls.greens + cls.reds:
            if name.startswith('_'):
                raise JitHintError("%s: the 'greens' and 'reds' names should"
                                   " not start with an underscore" % (cls,))
    _check_class = classmethod(_check_class)
