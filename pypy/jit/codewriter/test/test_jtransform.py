import random
from pypy.objspace.flow.model import FunctionGraph, Block, Link
from pypy.objspace.flow.model import SpaceOperation, Variable, Constant
from pypy.jit.codewriter.jtransform import Transformer
from pypy.jit.metainterp.history import getkind
from pypy.rpython.lltypesystem import lltype, rclass, rstr
from pypy.translator.unsimplify import varoftype
from pypy.jit.codewriter import heaptracker

class FakeRTyper:
    class type_system: name = 'lltypesystem'
    instance_reprs = {}

class FakeCPU:
    rtyper = FakeRTyper()
    def calldescrof(self, FUNC, ARGS, RESULT):
        return ('calldescr', FUNC, ARGS, RESULT)
    def fielddescrof(self, STRUCT, name):
        return ('fielddescr', STRUCT, name)
    def sizeof(self, STRUCT):
        return ('sizedescr', STRUCT)
    def sizevtableof(self, STRUCT, vtable):
        return ('sizevtabledescr', STRUCT, vtable)

class FakeLink:
    args = []
    def __init__(self, exitcase):
        self.exitcase = self.llexitcase = exitcase

class FakeResidualCallControl:
    def guess_call_kind(self, op):
        return 'residual'

class FakeRegularCallControl:
    def guess_call_kind(self, op):
        return 'regular'
    def graphs_from(self, op):
        return ['somegraph']
    def get_jitcode(self, graph, called_from=None):
        assert graph == 'somegraph'
        return 'somejitcode'


def test_optimize_goto_if_not():
    v1 = Variable()
    v2 = Variable()
    v3 = Variable(); v3.concretetype = lltype.Bool
    sp1 = SpaceOperation('foobar', [], None)
    sp2 = SpaceOperation('foobaz', [], None)
    block = Block([v1, v2])
    block.operations = [sp1, SpaceOperation('int_gt', [v1, v2], v3), sp2]
    block.exitswitch = v3
    block.exits = exits = [FakeLink(False), FakeLink(True)]
    res = Transformer().optimize_goto_if_not(block)
    assert res == True
    assert block.operations == [sp1, sp2]
    assert block.exitswitch == ('int_gt', v1, v2)
    assert block.exits == exits

def test_optimize_goto_if_not__incoming():
    v1 = Variable(); v1.concretetype = lltype.Bool
    block = Block([v1])
    block.exitswitch = v1
    block.exits = [FakeLink(False), FakeLink(True)]
    assert not Transformer().optimize_goto_if_not(block)

def test_optimize_goto_if_not__exit():
    v1 = Variable()
    v2 = Variable()
    v3 = Variable(); v3.concretetype = lltype.Bool
    block = Block([v1, v2])
    block.operations = [SpaceOperation('int_gt', [v1, v2], v3)]
    block.exitswitch = v3
    block.exits = [FakeLink(False), FakeLink(True)]
    block.exits[1].args = [v3]
    assert not Transformer().optimize_goto_if_not(block)

def test_optimize_goto_if_not__unknownop():
    v3 = Variable(); v3.concretetype = lltype.Bool
    block = Block([])
    block.operations = [SpaceOperation('foobar', [], v3)]
    block.exitswitch = v3
    block.exits = [FakeLink(False), FakeLink(True)]
    assert not Transformer().optimize_goto_if_not(block)

def test_optimize_goto_if_not__ptr_eq():
    for opname in ['ptr_eq', 'ptr_ne']:
        v1 = Variable()
        v2 = Variable()
        v3 = Variable(); v3.concretetype = lltype.Bool
        block = Block([v1, v2])
        block.operations = [SpaceOperation(opname, [v1, v2], v3)]
        block.exitswitch = v3
        block.exits = exits = [FakeLink(False), FakeLink(True)]
        res = Transformer().optimize_goto_if_not(block)
        assert res == True
        assert block.operations == []
        assert block.exitswitch == (opname, v1, v2)
        assert block.exits == exits

def test_optimize_goto_if_not__ptr_iszero():
    for opname in ['ptr_iszero', 'ptr_nonzero']:
        v1 = Variable()
        v3 = Variable(); v3.concretetype = lltype.Bool
        block = Block([v1])
        block.operations = [SpaceOperation(opname, [v1], v3)]
        block.exitswitch = v3
        block.exits = exits = [FakeLink(False), FakeLink(True)]
        res = Transformer().optimize_goto_if_not(block)
        assert res == True
        assert block.operations == []
        assert block.exitswitch == (opname, v1)
        assert block.exits == exits

def test_symmetric():
    ops = {'int_add': 'int_add',
           'int_or': 'int_or',
           'int_gt': ('int_gt', 'int_lt'),
           'uint_le': ('int_le', 'int_ge'),
           'char_ne': 'int_ne',
           'char_lt': ('int_lt', 'int_gt'),
           'int_add_nonneg_ovf': 'G_int_add_ovf',
           'uint_xor': 'int_xor',
           'float_mul': 'float_mul',
           'float_gt': ('float_gt', 'float_lt'),
           }
    v3 = varoftype(lltype.Signed)
    for v1 in [varoftype(lltype.Signed), Constant(42, lltype.Signed)]:
        for v2 in [varoftype(lltype.Signed), Constant(43, lltype.Signed)]:
            for name1, name2 in ops.items():
                op = SpaceOperation(name1, [v1, v2], v3)
                op1 = Transformer(FakeCPU()).rewrite_operation(op)
                if isinstance(name2, str):
                    name2 = name2, name2
                if isinstance(v1, Constant) and isinstance(v2, Variable):
                    assert op1.args == [v2, v1]
                    assert op1.result == v3
                    assert op1.opname == name2[1]
                else:
                    assert op1.args == [v1, v2]
                    assert op1.result == v3
                    assert op1.opname == name2[0]

def test_calls():
    for RESTYPE in [lltype.Signed, rclass.OBJECTPTR,
                    lltype.Float, lltype.Void]:
      for with_void in [False, True]:
        for with_i in [False, True]:
          for with_r in [False, True]:
            for with_f in [False, True]:
              ARGS = []
              if with_void: ARGS += [lltype.Void, lltype.Void]
              if with_i: ARGS += [lltype.Signed, lltype.Char]
              if with_r: ARGS += [rclass.OBJECTPTR, lltype.Ptr(rstr.STR)]
              if with_f: ARGS += [lltype.Float, lltype.Float]
              random.shuffle(ARGS)
              if with_f: expectedkind = 'irf'   # all kinds
              elif with_i: expectedkind = 'ir'  # integers and references
              else: expectedkind = 'r'          # only references
              yield residual_call_test, ARGS, RESTYPE, expectedkind
              yield direct_call_test, ARGS, RESTYPE, expectedkind

def get_direct_call_op(argtypes, restype):
    FUNC = lltype.FuncType(argtypes, restype)
    fnptr = lltype.functionptr(FUNC, "g")    # no graph
    c_fnptr = Constant(fnptr, concretetype=lltype.typeOf(fnptr))
    vars = [varoftype(TYPE) for TYPE in argtypes]
    v_result = varoftype(restype)
    op = SpaceOperation('direct_call', [c_fnptr] + vars, v_result)
    return op

def residual_call_test(argtypes, restype, expectedkind):
    op = get_direct_call_op(argtypes, restype)
    tr = Transformer(FakeCPU(), FakeResidualCallControl())
    op1 = tr.rewrite_operation(op)
    reskind = getkind(restype)[0]
    assert op1.opname == 'G_residual_call_%s_%s' % (expectedkind, reskind)
    assert op1.result == op.result
    assert op1.args[0] == op.args[0]
    FUNC = op.args[0].concretetype.TO
    NONVOIDARGS = tuple([ARG for ARG in FUNC.ARGS if ARG != lltype.Void])
    assert op1.args[1] == ('calldescr', FUNC, NONVOIDARGS, FUNC.RESULT)
    assert len(op1.args) == 2 + len(expectedkind)
    for sublist, kind1 in zip(op1.args[2:], expectedkind):
        assert sublist.kind.startswith(kind1)
        assert list(sublist) == [v for v in op.args[1:]
                                 if getkind(v.concretetype) == sublist.kind]
    for v in op.args[1:]:
        kind = getkind(v.concretetype)
        assert kind == 'void' or kind[0] in expectedkind

def direct_call_test(argtypes, restype, expectedkind):
    op = get_direct_call_op(argtypes, restype)
    tr = Transformer(FakeCPU(), FakeRegularCallControl())
    tr.graph = 'someinitialgraph'
    op1 = tr.rewrite_operation(op)
    reskind = getkind(restype)[0]
    assert op1.opname == 'G_inline_call_%s_%s' % (expectedkind, reskind)
    assert op1.result == op.result
    assert op1.args[0] == 'somejitcode'
    assert len(op1.args) == 1 + len(expectedkind)
    for sublist, kind1 in zip(op1.args[1:], expectedkind):
        assert sublist.kind.startswith(kind1)
        assert list(sublist) == [v for v in op.args[1:]
                                 if getkind(v.concretetype) == sublist.kind]
    for v in op.args[1:]:
        kind = getkind(v.concretetype)
        assert kind == 'void' or kind[0] in expectedkind

def test_getfield():
    # XXX a more compact encoding would be possible, something along
    # the lines of  getfield_gc_r %r0, $offset, %r1
    # which would not need a Descr at all.
    S1 = lltype.Struct('S1')
    S2 = lltype.GcStruct('S2')
    S  = lltype.GcStruct('S', ('int', lltype.Signed),
                              ('ps1', lltype.Ptr(S1)),
                              ('ps2', lltype.Ptr(S2)),
                              ('flt', lltype.Float),
                              ('boo', lltype.Bool),
                              ('chr', lltype.Char),
                              ('unc', lltype.UniChar))
    for name, suffix in [('int', 'i'),
                         ('ps1', 'i'),
                         ('ps2', 'r'),
                         ('flt', 'f'),
                         ('boo', 'i'),
                         ('chr', 'i'),
                         ('unc', 'i')]:
        v_parent = varoftype(lltype.Ptr(S))
        c_name = Constant(name, lltype.Void)
        v_result = varoftype(getattr(S, name))
        op = SpaceOperation('getfield', [v_parent, c_name], v_result)
        op1 = Transformer(FakeCPU()).rewrite_operation(op)
        assert op1.opname == 'getfield_gc_' + suffix
        fielddescr = ('fielddescr', S, name)
        assert op1.args == [v_parent, fielddescr]
        assert op1.result == v_result

def test_getfield_typeptr():
    v_parent = varoftype(rclass.OBJECTPTR)
    c_name = Constant('typeptr', lltype.Void)
    v_result = varoftype(rclass.OBJECT.typeptr)
    op = SpaceOperation('getfield', [v_parent, c_name], v_result)
    op1 = Transformer(FakeCPU()).rewrite_operation(op)
    assert op1.opname == 'G_guard_class'
    assert op1.args == [v_parent]
    assert op1.result == v_result

def test_setfield():
    # XXX a more compact encoding would be possible; see test_getfield()
    S1 = lltype.Struct('S1')
    S2 = lltype.GcStruct('S2')
    S  = lltype.GcStruct('S', ('int', lltype.Signed),
                              ('ps1', lltype.Ptr(S1)),
                              ('ps2', lltype.Ptr(S2)),
                              ('flt', lltype.Float),
                              ('boo', lltype.Bool),
                              ('chr', lltype.Char),
                              ('unc', lltype.UniChar))
    for name, suffix in [('int', 'i'),
                         ('ps1', 'i'),
                         ('ps2', 'r'),
                         ('flt', 'f'),
                         ('boo', 'i'),
                         ('chr', 'i'),
                         ('unc', 'i')]:
        v_parent = varoftype(lltype.Ptr(S))
        c_name = Constant(name, lltype.Void)
        v_newvalue = varoftype(getattr(S, name))
        op = SpaceOperation('setfield', [v_parent, c_name, v_newvalue],
                            varoftype(lltype.Void))
        op1 = Transformer(FakeCPU()).rewrite_operation(op)
        assert op1.opname == 'setfield_gc_' + suffix
        fielddescr = ('fielddescr', S, name)
        assert op1.args == [v_parent, fielddescr, v_newvalue]
        assert op1.result is None

def test_malloc_new():
    S = lltype.GcStruct('S')
    v = varoftype(lltype.Ptr(S))
    op = SpaceOperation('malloc', [Constant(S, lltype.Void),
                                   Constant({'flavor': 'gc'}, lltype.Void)], v)
    op1 = Transformer(FakeCPU()).rewrite_operation(op)
    assert op1.opname == 'new'
    assert op1.args == [('sizedescr', S)]

def test_malloc_new_with_vtable():
    class vtable: pass
    S = lltype.GcStruct('S', ('parent', rclass.OBJECT))
    heaptracker.set_testing_vtable_for_gcstruct(S, vtable, 'S')
    v = varoftype(lltype.Ptr(S))
    op = SpaceOperation('malloc', [Constant(S, lltype.Void),
                                   Constant({'flavor': 'gc'}, lltype.Void)], v)
    op1 = Transformer(FakeCPU()).rewrite_operation(op)
    assert op1.opname == 'new_with_vtable'
    assert op1.args == [('sizevtabledescr', S, vtable)]

def test_malloc_new_with_destructor():
    class vtable: pass
    S = lltype.GcStruct('S', ('parent', rclass.OBJECT))
    DESTRUCTOR = lltype.FuncType([lltype.Ptr(S)], lltype.Void)
    destructor = lltype.functionptr(DESTRUCTOR, 'destructor')
    lltype.attachRuntimeTypeInfo(S, destrptr=destructor)
    heaptracker.set_testing_vtable_for_gcstruct(S, vtable, 'S')
    v = varoftype(lltype.Ptr(S))
    op = SpaceOperation('malloc', [Constant(S, lltype.Void),
                                   Constant({'flavor': 'gc'}, lltype.Void)], v)
    tr = Transformer(FakeCPU(), FakeResidualCallControl())
    op1 = tr.rewrite_operation(op)
    assert op1.opname == 'G_residual_call_r_r'
    assert op1.args[0].value == 'alloc_with_del'    # pseudo-function as a str
    assert list(op1.args[2]) == []

def test_rename_on_links():
    v1 = Variable()
    v2 = Variable()
    v3 = Variable()
    block = Block([v1])
    block.operations = [SpaceOperation('cast_pointer', [v1], v2)]
    block2 = Block([v3])
    block.closeblock(Link([v2], block2))
    Transformer().optimize_block(block)
    assert block.inputargs == [v1]
    assert block.operations == []
    assert block.exits[0].target is block2
    assert block.exits[0].args == [v1]

def test_int_eq():
    v1 = varoftype(lltype.Signed)
    v2 = varoftype(lltype.Signed)
    v3 = varoftype(lltype.Bool)
    c0 = Constant(0, lltype.Signed)
    #
    for opname, reducedname in [('int_eq', 'int_is_zero'),
                                ('int_ne', 'int_is_true')]:
        op = SpaceOperation(opname, [v1, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == opname
        assert op1.args == [v1, v2]
        #
        op = SpaceOperation(opname, [v1, c0], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v1]
        #
        op = SpaceOperation(opname, [c0, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v2]

def test_ptr_eq():
    v1 = varoftype(rclass.OBJECTPTR)
    v2 = varoftype(rclass.OBJECTPTR)
    v3 = varoftype(lltype.Bool)
    c0 = Constant(lltype.nullptr(rclass.OBJECT), rclass.OBJECTPTR)
    #
    for opname, reducedname in [('ptr_eq', 'ptr_iszero'),
                                ('ptr_ne', 'ptr_nonzero')]:
        op = SpaceOperation(opname, [v1, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == opname
        assert op1.args == [v1, v2]
        #
        op = SpaceOperation(opname, [v1, c0], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v1]
        #
        op = SpaceOperation(opname, [c0, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v2]

def test_nongc_ptr_eq():
    v1 = varoftype(rclass.NONGCOBJECTPTR)
    v2 = varoftype(rclass.NONGCOBJECTPTR)
    v3 = varoftype(lltype.Bool)
    c0 = Constant(lltype.nullptr(rclass.NONGCOBJECT), rclass.NONGCOBJECTPTR)
    #
    for opname, reducedname in [('ptr_eq', 'int_is_zero'),
                                ('ptr_ne', 'int_is_true')]:
        op = SpaceOperation(opname, [v1, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == opname.replace('ptr_', 'int_')
        assert op1.args == [v1, v2]
        #
        op = SpaceOperation(opname, [v1, c0], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v1]
        #
        op = SpaceOperation(opname, [c0, v2], v3)
        op1 = Transformer().rewrite_operation(op)
        assert op1.opname == reducedname
        assert op1.args == [v2]
    #
    op = SpaceOperation('ptr_iszero', [v1], v3)
    op1 = Transformer().rewrite_operation(op)
    assert op1.opname == 'int_is_zero'
    assert op1.args == [v1]
    #
    op = SpaceOperation('ptr_nonzero', [v1], v3)
    op1 = Transformer().rewrite_operation(op)
    assert op1.opname == 'int_is_true'
    assert op1.args == [v1]
