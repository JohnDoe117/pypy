from pypy.rpython.lltypesystem import lltype, rffi
from pypy.objspace.flow.model import Block, Link, Constant, Variable
from pypy.objspace.flow.model import SpaceOperation, c_last_exception
from pypy.annotation import model as annmodel
from pypy.rpython.annlowlevel import MixLevelHelperAnnotator
from pypy.translator.exceptiontransform import error_constant


class ExceptionTransformer(object):

    def __init__(self, translator):
        self.translator = translator
        edata = translator.rtyper.getexceptiondata()
        lltype_of_exception_value = edata.lltype_of_exception_value
        lltype_of_exception_type  = edata.lltype_of_exception_type
        self.lltype_of_exception_value = lltype_of_exception_value
        self.lltype_of_exception_type  = lltype_of_exception_type
        self.mixlevelannotator = MixLevelHelperAnnotator(translator.rtyper)

        def rpyexc_occured():
            return False

        def rpyexc_fetch_type():
            return lltype.nullptr(lltype_of_exception_type.TO)

        def rpyexc_fetch_value():
            return lltype.nullptr(lltype_of_exception_value.TO)

        def rpyexc_clear():
            pass

        exit = rffi.llexternal('return', [lltype.Signed], lltype.Void,
                               _nowrapper=True)
        def rpyexc_raise(etype, evalue):
            exit(0)     # XXX!

        self.rpyexc_occured_ptr = self.build_func(
            "RPyExceptionOccurred",
            rpyexc_occured,
            [], lltype.Bool)

        self.rpyexc_fetch_type_ptr = self.build_func(
            "RPyFetchExceptionType",
            rpyexc_fetch_type,
            [], self.lltype_of_exception_type)

        self.rpyexc_fetch_value_ptr = self.build_func(
            "RPyFetchExceptionValue",
            rpyexc_fetch_value,
            [], self.lltype_of_exception_value)

        self.rpyexc_clear_ptr = self.build_func(
            "RPyClearException",
            rpyexc_clear,
            [], lltype.Void)

        self.rpyexc_raise_ptr = self.build_func(
            "RPyRaiseException",
            rpyexc_raise,
            [self.lltype_of_exception_type, self.lltype_of_exception_value],
            lltype.Void,
            jitcallkind='rpyexc_raise') # for the JIT

        self.mixlevelannotator.finish()

    def build_func(self, name, fn, inputtypes, rettype, **kwds):
        l2a = annmodel.lltype_to_annotation
        graph = self.mixlevelannotator.getgraph(fn, map(l2a, inputtypes), l2a(rettype))
        return self.constant_func(name, inputtypes, rettype, graph, 
                                  exception_policy="exc_helper", **kwds)

    def constant_func(self, name, inputtypes, rettype, graph, **kwds):
        FUNC_TYPE = lltype.FuncType(inputtypes, rettype)
        fn_ptr = lltype.functionptr(FUNC_TYPE, name, graph=graph, **kwds)
        return Constant(fn_ptr, lltype.Ptr(FUNC_TYPE))

    def create_exception_handling(self, graph):
        for block in list(graph.iterblocks()):
            self.transform_block(graph, block)
        self.transform_except_block(graph, graph.exceptblock)

    def transform_block(self, graph, block):
        if block.exitswitch == c_last_exception:
            assert block.exits[0].exitcase is None
            block.exits = block.exits[:1]
            block.exitswitch = None

    def transform_except_block(self, graph, block):
        # attach an except block -- let's hope that nobody uses it
        graph.exceptblock = Block([Variable('etype'),   # exception class
                                   Variable('evalue')])  # exception value
        graph.exceptblock.operations = ()
        graph.exceptblock.closeblock()
        
        result = Variable()
        result.concretetype = lltype.Void
        block.operations = [SpaceOperation(
           "direct_call", [self.rpyexc_raise_ptr] + block.inputargs, result)]
        l = Link([error_constant(graph.returnblock.inputargs[0].concretetype)], graph.returnblock)
        block.recloseblock(l)
