import py

from pypy import conftest
from pypy.translator.translator import graphof
from pypy.jit.timeshifter.test.test_timeshift import TestLLType as TSTestLLType, getargtypes
from pypy.jit.rainbow.test.test_interpreter import P_NOVIRTUAL, StopAtXPolicy
from pypy.jit.rainbow.test.test_interpreter import hannotate, InterpretationTest
from pypy.jit.rainbow.test.test_vlist import P_OOPSPEC
from pypy.rpython.llinterp import LLInterpreter
from pypy.rpython.lltypesystem import lltype
from pypy.objspace.flow.model import  summary
from pypy.rlib.jit import hint
from pypy.jit.codegen.llgraph.rgenop import RGenOp as LLRGenOp

class PortalTest(InterpretationTest):
    RGenOp = LLRGenOp
    small = True

    def _timeshift_from_portal(self, main, portal, main_args,
                              inline=None, policy=None,
                              backendoptimize=False):
        # decode the 'values' if they are specified as strings
        if hasattr(main, 'convert_arguments'):
            assert len(main.convert_arguments) == len(main_args)
            main_args = [decoder(value) for decoder, value in zip(
                                        main.convert_arguments,
                                        main_args)]
        key = main, portal, inline, policy, backendoptimize
        try:
            cache, argtypes = self._cache[key]
        except KeyError:
            pass
        else:
            self.__dict__.update(cache)
            assert argtypes == getargtypes(self.rtyper.annotator, main_args)
            return main_args

        self._serialize(main, main_args, portal=portal,
                        policy=policy, inline=inline,
                        backendoptimize=backendoptimize)

        if conftest.option.view and self.small:
            self.rtyper.annotator.translator.view()

        # Populate the cache
        if len(self._cache_order) >= 3:
            del self._cache[self._cache_order.pop(0)]
        cache = self.__dict__.copy()
        self._cache[key] = cache, getargtypes(self.rtyper.annotator, main_args)
        self._cache_order.append(key)
        return main_args

    
    def timeshift_from_portal(self, main, portal, main_args,
                              inline=None, policy=None,
                              backendoptimize=False):
        main_args = self._timeshift_from_portal(main, portal, main_args,
                                                inline=inline, policy=policy,
                                                backendoptimize=backendoptimize)
        self.main_args = main_args
        self.main_is_portal = main is portal
        llinterp = LLInterpreter(self.rtyper)
        res = llinterp.eval_graph(self.maingraph, main_args)
        return res

    def get_residual_graph(self):
        llinterp = LLInterpreter(self.rtyper)
        portalstate = self.rewriter.state
        if self.main_is_portal:
            residual_graph = portalstate.readportal(*self.main_args)._obj.graph
        else:
            residual_graphs = portalstate.readallportals()
            assert len(residual_graphs) == 1
            residual_graph = residual_graphs[0]._obj.graph
        return residual_graph
            
    def count_direct_calls(self):
        residual_graph = self.get_residual_graph()
        calls = {}
        for block in residual_graph.iterblocks():
            for op in block.operations:
                if op.opname == 'direct_call':
                    graph = getattr(op.args[0].value._obj, 'graph', None)
                    calls[graph] = calls.get(graph, 0) + 1
        return calls
        

class TestPortal(PortalTest):
    type_system = "lltype"
            
    def test_simple(self):

        def main(code, x):
            return evaluate(code, x)

        def evaluate(y, x):
            hint(y, concrete=True)
            z = y+x
            return z

        res = self.timeshift_from_portal(main, evaluate, [3, 2])
        assert res == 5
        self.check_insns({"int_add": 1})

        res = self.timeshift_from_portal(main, evaluate, [3, 5])
        assert res == 8
        self.check_insns({"int_add": 1})

        res = self.timeshift_from_portal(main, evaluate, [4, 7])
        assert res == 11
    
    def test_main_as_portal(self):
        def main(x):
            return x

        res = self.timeshift_from_portal(main, main, [42])
        assert res == 42
        self.check_insns({})

    def test_multiple_portal_calls(self):
        def ll_function(n):
            hint(None, global_merge_point=True)
            k = n
            if k > 5:
                k //= 2
            k = hint(k, promote=True)
            k *= 17
            return hint(k, variable=True)

        res = self.timeshift_from_portal(ll_function, ll_function, [4],
                                         policy=P_NOVIRTUAL)
        assert res == 68
        self.check_insns(int_floordiv=1, int_mul=0)

        res = self.timeshift_from_portal(ll_function, ll_function, [4],
                                         policy=P_NOVIRTUAL)
        assert res == 68
        self.check_insns(int_floordiv=1, int_mul=0)

    def test_dfa_compile(self):
        from pypy.lang.automata.dfa import getautomaton, convertdfa, recognizetable
        a = getautomaton()
        dfatable, final_states = convertdfa(a)
        def main(gets):
            s = ["aaaaaaaaaab", "aaaa"][gets]
            return recognizetable(dfatable, s, final_states)

        # must backendoptimize to remove the mallocs related
        # to the interior ptrs
        res = self.timeshift_from_portal(main, recognizetable, [0],
                                         policy=P_NOVIRTUAL,
                                         backendoptimize=True)
        assert res

        res = self.timeshift_from_portal(main, recognizetable, [1],
                                         policy=P_NOVIRTUAL,
                                         backendoptimize=True)
        assert not res

    def test_dfa_compile2(self):
        from pypy.lang.automata.dfa import getautomaton, convertagain, recognizeparts
        more = [convertagain(getautomaton()), convertagain(getautomaton())]
        def main(gets, gets2):
            alltrans, final_states = more[gets2]
            s = ["aaaaaaaaaab", "aaaa"][gets]
            return recognizeparts(alltrans, final_states, s)

        # must backendoptimize to remove the mallocs related
        # to the interior ptrs
        res = self.timeshift_from_portal(main, recognizeparts, [0, 0],
                                         policy=P_NOVIRTUAL,
                                         backendoptimize=True)
        assert res

        # XXX unfortunately we have to create a new version each time - because of pbc
        res = self.timeshift_from_portal(main, recognizeparts, [1, 0],
                                         policy=P_NOVIRTUAL,
                                         backendoptimize=True)
        assert not res

    def test_dfa_compile3(self):
        from pypy.lang.automata.dfa import getautomaton, recognize3
        def main(gets):
            auto = getautomaton()
            s = ["aaaaaaaaaab", "aaaa"][gets]
            return recognize3(auto, s)

        res = self.timeshift_from_portal(main, recognize3, [0],
                                         policy=P_OOPSPEC)
        assert res

        res = self.timeshift_from_portal(main, recognize3, [1],
                                         policy=P_OOPSPEC)
        assert not res

    def test_method_call_nonpromote(self):
        class Base(object):
            pass
        class Int(Base):
            def __init__(self, n):
                self.n = n
            def double(self):
                return Int(self.n * 2)
            def get(self):
                return self.n
        class Str(Base):
            def __init__(self, s):
                self.s = s
            def double(self):
                return Str(self.s + self.s)
            def get(self):
                return ord(self.s[4])

        def ll_main(n):
            if n > 0:
                o = Int(n)
            else:
                o = Str('123')
            return ll_function(o)

        def ll_function(o):
            hint(None, global_merge_point=True)
            return o.double().get()

        res = self.timeshift_from_portal(ll_main, ll_function, [5], policy=P_NOVIRTUAL)
        assert res == 10
        self.check_insns(indirect_call=2)

        res = self.timeshift_from_portal(ll_main, ll_function, [0], policy=P_NOVIRTUAL)
        assert res == ord('2')
        self.check_insns(indirect_call=2)

    def test_method_call_promote(self):
        class Base(object):
            pass
        class Int(Base):
            def __init__(self, n):
                self.n = n
            def double(self):
                return Int(self.n * 2)
            def get(self):
                return self.n
        class Str(Base):
            def __init__(self, s):
                self.s = s
            def double(self):
                return Str(self.s + self.s)
            def get(self):
                return ord(self.s[4])

        def ll_main(n):
            if n > 0:
                o = Int(n)
            else:
                o = Str('123')
            return ll_function(o)

        def ll_function(o):
            hint(None, global_merge_point=True)
            hint(o.__class__, promote=True)
            return o.double().get()

        res = self.timeshift_from_portal(ll_main, ll_function, [5], policy=P_NOVIRTUAL)
        assert res == 10
        self.check_insns(indirect_call=0)

        res = self.timeshift_from_portal(ll_main, ll_function, [0], policy=P_NOVIRTUAL)
        assert res == ord('2')
        self.check_insns(indirect_call=0)

    def test_isinstance(self):
        class Base(object):
            pass
        class Int(Base):
            def __init__(self, n):
                self.n = n
        class Str(Base):
            def __init__(self, s):
                self.s = s

        def ll_main(n):
            if n > 0:
                o = Int(n)
            else:
                o = Str('123')
            return ll_function(o)

        def ll_function(o):
            hint(o, deepfreeze=True)
            hint(o, concrete=True)
            x = isinstance(o, Str)
            return x
            

        res = self.timeshift_from_portal(ll_main, ll_function, [5], policy=P_NOVIRTUAL)
        assert not res

    def test_greenmethod_call_nonpromote(self):
        class Base(object):
            pass
        class Int(Base):
            def __init__(self, n):
                self.n = n
            def tag(self):
                return 123
        class Str(Base):
            def __init__(self, s):
                self.s = s
            def tag(self):
                return 456

        def ll_main(n):
            if n > 0:
                o = Int(n)
            else:
                o = Str('123')
            return ll_function(o)

        def ll_function(o):
            hint(None, global_merge_point=True)
            return o.tag()

        res = self.timeshift_from_portal(ll_main, ll_function, [5], policy=P_NOVIRTUAL)
        assert res == 123
        self.check_insns(indirect_call=1)

    def test_cast_ptr_to_int(self):
        GCS1 = lltype.GcStruct('s1', ('x', lltype.Signed))
        def g(p):
            return lltype.cast_ptr_to_int(p)
        def f():
            p = lltype.malloc(GCS1)
            return g(p) - lltype.cast_ptr_to_int(p)

        res = self.timeshift_from_portal(f, g, [], policy=P_NOVIRTUAL)
        assert res == 0


    def test_virt_obj_method_call_promote(self):
        class Base(object):
            pass
        class Int(Base):
            def __init__(self, n):
                self.n = n
            def double(self):
                return Int(self.n * 2)
            def get(self):
                return self.n
        class Str(Base):
            def __init__(self, s):
                self.s = s
            def double(self):
                return Str(self.s + self.s)
            def get(self):
                return ord(self.s[4])

        def ll_make(n):
            if n > 0:
                return Int(n)
            else:
                return Str('123')

        def ll_function(n):
            hint(None, global_merge_point=True)
            o = ll_make(n)
            hint(o.__class__, promote=True)
            return o.double().get()

        res = self.timeshift_from_portal(ll_function, ll_function, [5],
                                         policy=StopAtXPolicy(ll_make))
        assert res == 10
        self.check_insns(indirect_call=0, malloc=0)

        res = self.timeshift_from_portal(ll_function, ll_function, [0],
                                         policy=StopAtXPolicy(ll_make))
        assert res == ord('2')
        self.check_insns(indirect_call=0, malloc=0)
