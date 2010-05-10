import py
from pypy.jit.codewriter.codewriter import CodeWriter
from pypy.jit.codewriter import support
from pypy.rpython.lltypesystem import lltype, llmemory

class FakeRTyper:
    class annotator:
        translator = None
    class type_system:
        name = 'lltypesystem'
    def getcallable(self, graph):
        F = lltype.FuncType([], lltype.Signed)
        return lltype.functionptr(F, 'bar')

class FakeCPU:
    rtyper = FakeRTyper()
    def calldescrof(self, FUNC, ARGS, RESULT):
        return ('calldescr', FUNC, ARGS, RESULT)

class FakePolicy:
    def look_inside_graph(self, graph):
        return True


def test_loop():
    def f(a, b):
        while a > 0:
            b += a
            a -= 1
        return b
    cw = CodeWriter()
    jitcode = cw.transform_func_to_jitcode(f, [5, 6])
    assert jitcode.code == ("\x00\x10\x00\x00\x00"
                            "\x01\x01\x00\x01"
                            "\x02\x00\x01\x00"
                            "\x03\x00\x00"
                            "\x04\x01")
    assert cw.assembler.insns == {'goto_if_not_int_gt/Lic': 0,
                                  'int_add/iii': 1,
                                  'int_sub/ici': 2,
                                  'goto/L': 3,
                                  'int_return/i': 4}
    assert jitcode.num_regs_i() == 2
    assert jitcode.num_regs_r() == 0
    assert jitcode.num_regs_f() == 0
    assert jitcode._live_vars(0) == '%i0 %i1'
    for i in range(1, len(jitcode.code)):
        py.test.raises(KeyError, jitcode._live_vars, i)

def test_call():
    def ggg(x):
        return x * 2
    def fff(a, b):
        return ggg(b) - ggg(a)
    rtyper = support.annotate(fff, [35, 42])
    maingraph = rtyper.annotator.translator.graphs[0]
    cw = CodeWriter(FakeCPU())
    jitcode = cw.make_jitcodes(maingraph, FakePolicy(), verbose=True)
    print jitcode._dump
    [jitcode2] = cw.assembler.descrs
    print jitcode2._dump
    assert jitcode is not jitcode2
    assert jitcode.name == 'fff'
    assert jitcode2.name == 'ggg'
    assert 'ggg' in jitcode._dump
    assert lltype.typeOf(jitcode2.fnaddr) == llmemory.Address
    assert jitcode2.calldescr[0] == 'calldescr'

def test_integration():
    from pypy.jit.metainterp.blackhole import BlackholeInterpBuilder
    def f(a, b):
        while a > 2:
            b += a
            a -= 1
        return b
    cw = CodeWriter()
    jitcode = cw.transform_func_to_jitcode(f, [5, 6])
    blackholeinterpbuilder = BlackholeInterpBuilder(cw)
    blackholeinterp = blackholeinterpbuilder.acquire_interp()
    blackholeinterp.setarg_i(0, 6)
    blackholeinterp.setarg_i(1, 100)
    blackholeinterp.run(jitcode, 0)
    assert blackholeinterp.get_result_i() == 100+6+5+4+3
