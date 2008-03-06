import py
from pypy.rlib.unroll import unrolling_iterable
from pypy.rlib.objectmodel import we_are_translated
from pypy.objspace.flow import model as flowmodel
from pypy.rpython.annlowlevel import cachedtype
from pypy.rpython.lltypesystem import lltype, llmemory
from pypy.jit.hintannotator.model import originalconcretetype
from pypy.jit.hintannotator import model as hintmodel
from pypy.jit.timeshifter import rtimeshift, rvalue, rcontainer, exception
from pypy.jit.timeshifter import oop
from pypy.jit.timeshifter.greenkey import KeyDesc
from pypy.jit.rainbow.interpreter import JitCode, JitInterpreter
from pypy.translator.backendopt.removenoops import remove_same_as
from pypy.translator.backendopt.ssa import SSA_to_SSI
from pypy.translator.unsimplify import varoftype

def residual_exception_nontranslated(jitstate, e, rtyper):
    # since we have a normal exception instance here
    # we need to turn it into a low level one
    assert not we_are_translated()
    bk = rtyper.annotator.bookkeeper
    exc_classdef = bk.getuniqueclassdef(e.__class__)
    ll_exc = rtyper.exceptiondata.get_standard_ll_exc_instance(
        rtyper, exc_classdef)
    jitstate.residual_ll_exception(ll_exc)

class CallDesc:
    __metaclass__ = cachedtype

    def __init__(self, RGenOp, rtyper, FUNCTYPE, voidargs=()):
        self.sigtoken = RGenOp.sigToken(FUNCTYPE.TO)
        self.result_kind = RGenOp.kindToken(FUNCTYPE.TO.RESULT)
        # xxx what if the result is virtualizable?
        self.redboxbuilder = rvalue.ll_redboxbuilder(FUNCTYPE.TO.RESULT)
        whatever_return_value = FUNCTYPE.TO.RESULT._defl()
        numargs = len(FUNCTYPE.TO.ARGS)
        voidargcount = 0
        for ARG in FUNCTYPE.TO.ARGS:
            if ARG == lltype.Void:
                voidargcount += 1
        if len(voidargs) != voidargcount:
            voidargs = (None, ) * voidargcount
        argiter = unrolling_iterable(FUNCTYPE.TO.ARGS)
        RETURN = FUNCTYPE.TO.RESULT
        def green_call(interpreter, fnptr_gv, greenargs):
            fnptr = fnptr_gv.revealconst(FUNCTYPE)
            assert len(greenargs) + len(voidargs) == numargs 
            args = ()
            j = 0
            k = 0
            for ARG in argiter:
                if ARG == lltype.Void:
                    # XXX terrible hack
                    if not we_are_translated():
                        arg = voidargs[k]
                        arg._TYPE = lltype.Void
                    else:
                        arg = None
                    args += (arg, )
                    k += 1
                else:
                    genconst = greenargs[j]
                    arg = genconst.revealconst(ARG)
                    args += (arg, )
                    j += 1
            rgenop = interpreter.jitstate.curbuilder.rgenop
            try:
                result = fnptr(*args)
            except Exception, e:
                if not we_are_translated():
                    residual_exception_nontranslated(interpreter.jitstate, e, rtyper)
                else:
                    interpreter.jitstate.residual_exception(e)
                result = whatever_return_value
            if RETURN != lltype.Void:
                interpreter.green_result(rgenop.genconst(result))
        self.green_call = green_call

    def _freeze_(self):
        return True


class IndirectCallsetDesc(object):
    __metaclass__ = cachedtype
    
    def __init__(self, graph2tsgraph, codewriter):

        keys = []
        values = []
        common_args_r = None
        for graph, tsgraph in graph2tsgraph:
            fnptr    = codewriter.rtyper.getcallable(graph)
            keys.append(llmemory.cast_ptr_to_adr(fnptr))
            values.append(codewriter.get_jitcode(tsgraph))

        def bytecode_for_address(fnaddress):
            # XXX optimize
            for i in range(len(keys)):
                if keys[i] == fnaddress:
                    return values[i]

        self.bytecode_for_address = bytecode_for_address

        self.graphs = [graph for (graph, tsgraph) in graph2tsgraph]
        self.jitcodes = values
        self.calldesc = CallDesc(codewriter.RGenOp, codewriter.rtyper,
                                 lltype.typeOf(fnptr))


class BytecodeWriter(object):
    def __init__(self, t, hannotator, RGenOp):
        self.translator = t
        self.rtyper = hannotator.base_translator.rtyper
        self.hannotator = hannotator
        etrafo = hannotator.exceptiontransformer
        type_system = self.rtyper.type_system.name
        self.exceptiondesc = exception.ExceptionDesc(
            RGenOp, etrafo, type_system, True)
        self.interpreter = JitInterpreter(self.exceptiondesc, RGenOp)
        self.RGenOp = RGenOp
        self.current_block = None
        self.raise_analyzer = hannotator.exceptiontransformer.raise_analyzer
        self.all_graphs = {} # mapping graph to bytecode
        self.unfinished_graphs = []
        self.num_global_mergepoints = 0
        self.ptr_to_jitcode = {}
        self.transformer = GraphTransformer(hannotator)

    def can_raise(self, op):
        return self.raise_analyzer.analyze(op)

    def make_bytecode(self, graph, is_portal=True):
        self.transformer.transform_graph(graph)
        #graph.show()
        if is_portal:
            bytecode = JitCode.__new__(JitCode)
            bytecode.name = graph.name     # for dump()
            bytecode.is_portal = True
            self.all_graphs[graph] = bytecode
        self.seen_blocks = {}
        self.assembler = []
        self.constants = []
        self.typekinds = []
        self.redboxclasses = []
        self.keydescs = []
        self.structtypedescs = []
        self.fielddescs = []
        self.arrayfielddescs = []
        self.interiordescs = []
        self.exceptioninstances = []
        self.oopspecdescs = []
        self.promotiondescs = []
        self.called_bytecodes = []
        self.num_local_mergepoints = 0
        self.graph_color = self.graph_calling_color(graph)
        self.calldescs = []
        self.indirectcalldescs = []
        self.metacalldescs = []
        self.is_portal = is_portal
        # mapping constant -> index in constants
        self.const_positions = {}
        # mapping blocks to True
        self.seen_blocks = {}
        self.redvar_positions = {}
        # mapping block to the free red index
        self.free_red = {}
        self.greenvar_positions = {}
        # mapping block to the free green index
        self.free_green = {}
        # mapping TYPE to index
        self.type_positions = {}
        # mapping tuples of green TYPES to index
        self.keydesc_positions = {}
        # mapping STRUCTS to index
        self.structtypedesc_positions = {}
        # mapping tuples of STRUCT, name to index
        self.fielddesc_positions = {}
        # mapping ARRAYS to index
        self.arrayfielddesc_positions = {}
        # mapping (TYPE, path) to index
        self.interiordesc_positions = {}
        # mapping exception class to index
        self.exceptioninstance_positions = {}
        # mapping (fnobj, can_raise) to index
        self.oopspecdesc_positions = {}
        # mapping (fnobj, can_raise) to index
        self.promotiondesc_positions = {}
        # mapping graphs to index
        self.graph_positions = {}
        # mapping fnobjs to index
        self.calldesc_positions = {}
        # mapping class to index
        self.metacalldesc_positions = {}
        # mapping fnobjs to index
        self.indirectcalldesc_positions = {}

        self.graph = graph
        self.mergepoint_set = {}
        self.compute_merge_points()

        self.make_bytecode_block(graph.startblock)
        assert self.current_block is None
        bytecode = self.all_graphs[graph]
        labelpos = {}
        code = assemble_labelpos(labelpos, self.interpreter, *self.assembler)
        bytecode.__init__(graph.name,
                          code,
                          self.constants,
                          self.typekinds,
                          self.redboxclasses,
                          self.keydescs,
                          self.structtypedescs,
                          self.fielddescs,
                          self.arrayfielddescs,
                          self.interiordescs,
                          self.exceptioninstances,
                          self.oopspecdescs,
                          self.promotiondescs,
                          self.called_bytecodes,
                          self.num_local_mergepoints,
                          self.graph_color,
                          self.calldescs,
                          self.metacalldescs,
                          self.indirectcalldescs,
                          self.is_portal)
        bytecode._source = self.assembler
        bytecode._interpreter = self.interpreter
        bytecode._labelpos = labelpos
        bytecode.dump()
        if is_portal:
            self.finish_all_graphs()
            self.interpreter.set_num_global_mergepoints(
                self.num_global_mergepoints)
            return bytecode

    def get_jitcode(self, graph):
        if graph in self.all_graphs:
            return self.all_graphs[graph]
        bytecode = JitCode.__new__(JitCode)
        bytecode.name = graph.name     # for dump()
        self.all_graphs[graph] = bytecode
        self.unfinished_graphs.append(graph)
        return bytecode

    def finish_all_graphs(self):
        while self.unfinished_graphs:
            graph = self.unfinished_graphs.pop()
            self.make_bytecode(graph, is_portal=False)

    def compute_merge_points(self):
        entrymap = flowmodel.mkentrymap(self.graph)
        startblock = self.graph.startblock
        global_merge_blocks = {}
        for block in self.graph.iterblocks():
            if not block.operations:
                continue
            op = block.operations[0]
            hashint = False
            cand = 0
            if (op.opname == 'hint' and
                op.args[1].value == {'global_merge_point': True}):
                assert self.is_portal, "global_merge_point can appare only in portal"
                hashint = True
                if block is startblock or len(entrymap[block]) > 1:
                    global_merge_blocks[block] = True
                    cand += 1
                else:
                    prevblock = entrymap[block][0].prevblock
                    if len(entrymap[prevblock]) > 1:
                        global_merge_blocks[prevblock] = True
                        cand += 1
            assert not hashint or cand==1, (
                "ambigous global merge point hint: %r" % block)
            for op in block.operations[1:]:
                assert not (op.opname == 'hint' and
                    op.args[1].value == {'global_merge_point': True}), (
                    "stranded global merge point hint: %r" % block)
                
        for block, links in entrymap.items():
            if len(links) > 1 and block is not self.graph.returnblock:
                if block in global_merge_blocks:
                    self.mergepoint_set[block] = 'global'
                else:
                    self.mergepoint_set[block] = 'local'
        if startblock in global_merge_blocks:
            self.mergepoint_set[startblock] = 'global'

    def make_bytecode_block(self, block, insert_goto=False):
        if block in self.seen_blocks:
            if insert_goto:
                self.emit("goto")
                self.emit(tlabel(block))
            return
        # inserting a goto not necessary, falling through
        self.seen_blocks[block] = True
        oldblock = self.current_block
        self.free_green[block] = 0
        self.free_red[block] = 0
        self.current_block = block

        self.emit(label(block))
        reds, greens = self.sort_by_color(block.inputargs)
        for arg in reds:
            self.register_redvar(arg, verbose=False)
        for arg in greens:
            self.register_greenvar(arg, verbose=False)
        self.insert_merges(block)
        for op in block.operations:
            self.serialize_op(op)
        self.insert_exits(block)
        self.current_block = oldblock

    def insert_exits(self, block):
        if block.exits == ():
            returnvar, = block.inputargs
            color = self.graph_calling_color(self.graph)
            if self.is_portal:
                if color == "yellow":
                    place = self.serialize_oparg("red", returnvar)
                    assert place == 0
                if color == "gray":
                    self.emit("gray_return")
                else:
                    self.emit("red_return")
            elif color == "red":
                self.emit("red_return")
            elif color == "gray":
                self.emit("gray_return")
            elif color == "yellow":
                self.emit("yellow_return")
            else:
                assert 0, "unknown graph calling color %s" % (color, )
        elif len(block.exits) == 1:
            link, = block.exits
            self.emit(*self.insert_renaming(link))
            self.make_bytecode_block(link.target, insert_goto=True)
        elif len(block.exits) == 2:
            linkfalse, linktrue = block.exits
            if linkfalse.llexitcase == True:
                linkfalse, linktrue = linktrue, linkfalse
            color = self.varcolor(block.exitswitch)
            index = self.serialize_oparg(color, block.exitswitch)
            reverse = None
            if color == "red":
                srcopname, srcargs = self.trace_back_bool_var(
                    block, block.exitswitch)
                if srcopname is not None:
                    if srcopname == 'ptr_nonzero':
                        reverse = False
                    elif srcopname == 'ptr_iszero':
                        reverse = True

            falserenaming = self.insert_renaming(linkfalse)
            truerenaming = self.insert_renaming(linktrue)
            if reverse is not None:
                ptrindex = self.serialize_oparg("red", srcargs[0])
                self.emit("red_goto_ifptrnonzero")
                self.emit(reverse)
                self.emit(ptrindex)
            else:
                self.emit("%s_goto_iftrue" % color)
            self.emit(index)
            self.emit(tlabel(linktrue))
            self.emit(*falserenaming)
            self.make_bytecode_block(linkfalse.target, insert_goto=True)
            self.emit(label(linktrue))
            self.emit(*truerenaming)
            self.make_bytecode_block(linktrue.target, insert_goto=True)
        else:
            assert self.varcolor(block.exitswitch) == "green"
            for link in block.exits:
                if link.exitcase == 'default':
                    defaultlink = link
            switchlinks = [link for link in block.exits
                               if link is not defaultlink]
            
            renamings = [self.insert_renaming(link) for link in switchlinks]
            defaultrenaming = self.insert_renaming(defaultlink)
            cases = [flowmodel.Constant(link.exitcase,
                                        block.exitswitch.concretetype)
                         for link in switchlinks]
            cases = [self.serialize_oparg("green", case) for case in cases]
            targets = [tlabel(link) for link in switchlinks]
            self.emit("green_switch")
            self.emit(self.serialize_oparg("green", block.exitswitch))
            self.emit(len(cases), *cases)
            self.emit(len(targets), *targets)
            self.emit(*defaultrenaming)
            self.make_bytecode_block(defaultlink.target, insert_goto=True)
            for renaming, link in zip(renamings, switchlinks):
                self.emit(label(link))
                self.emit(*renaming)
                self.make_bytecode_block(link.target, insert_goto=True)

    def insert_merges(self, block):
        if block is self.graph.returnblock:
            return
        if block not in self.mergepoint_set:
            return
        # make keydesc
        key = ()
        for arg in self.sort_by_color(block.inputargs)[1]:
            TYPE = arg.concretetype
            key += (TYPE, )
        if not key:
            keyindex = -1 # use prebuilt empty_key
        elif key not in self.keydesc_positions:
            keyindex = len(self.keydesc_positions)
            self.keydesc_positions[key] = keyindex
            self.keydescs.append(KeyDesc(self.RGenOp, *key))
        else:
            keyindex = self.keydesc_positions[key]

        kind = self.mergepoint_set[block]
        if kind == "global":
            self.emit("guard_global_merge")
            num = self.num_global_mergepoints
            self.num_global_mergepoints += 1
        else:
            num = self.num_local_mergepoints
            self.num_local_mergepoints += 1
        self.emit("%s_merge" % kind)
        self.emit(num)
        self.emit(keyindex)

    def insert_renaming(self, link):
        reds, greens = self.sort_by_color(link.args, link.target.inputargs)
        result = []
        for color, args in [("red", reds), ("green", greens)]:
            result += ["make_new_%svars" % (color, ), len(args)]
            for v in args:
                result.append(self.serialize_oparg(color, v))
        return result

    def serialize_op(self, op):
        specialcase = getattr(self, "serialize_op_%s" % (op.opname, ), None)
        if specialcase is not None:
            try:
                return specialcase(op)
            except NotImplementedError:
                pass
        color = self.opcolor(op)
        args = []
        for arg in op.args:
            args.append(self.serialize_oparg(color, arg))
        opdesc = self.serialize_opcode(color, op)
        self.emit(*args)
        if self.hannotator.binding(op.result).is_green():
            self.register_greenvar(op.result)
        else:
            self.register_redvar(op.result)
        if (opdesc is not None and 
            opdesc.tryfold and not opdesc.canfold and opdesc.canraise):
            exc_class = opdesc.llop.canraise[0]
            self.emit("split_raisingop",
                      self.exceptioninstance_position(exc_class))

    def serialize_opcode(self, color, op):
        opname = op.opname
        name = "%s_%s" % (color, opname)
        index = self.interpreter.find_opcode(name)
        if index == -1:
            opdesc = rtimeshift.make_opdesc(
                self.RGenOp, opname,
                [self.hannotator.binding(arg) for arg in op.args],
                self.hannotator.binding(op.result), )
            index = self.interpreter.make_opcode_implementation(color, opdesc)
        self.emit(name)
        return self.interpreter.opcode_descs[index]

    def serialize_oparg(self, color, arg):
        if color == "red":
            if self.varcolor(arg) == "green":
                return self.convert_to_red(arg)
            return self.redvar_position(arg)
        elif color == "green":
            return self.green_position(arg)
        assert 0, "unknown color"

    def convert_to_red(self, arg):
        block = self.current_block
        if (arg, block) in self.redvar_positions:
            # already converted
            return self.redvar_positions[arg, block]
        self.emit("make_redbox")
        argindex = self.green_position(arg)
        self.emit(argindex)
        self.emit(self.type_position(arg.concretetype))
        resultindex = self.register_redvar((arg, block))
        return resultindex

    def opcolor(self, op):
        for v in op.args:
            if not self.hannotator.binding(v).is_green():
                return "red"
        if not self.hannotator.binding(op.result).is_green():
            return "red"
        return "green"

    def varcolor(self, var):
        if self.hannotator.binding(var).is_green():
            color = "green"
        else:
            color = "red"
        return color
        
    def register_redvar(self, arg, where=-1, verbose=True):
        assert arg not in self.redvar_positions
        if where == -1:
            where = self.free_red[self.current_block]
            self.free_red[self.current_block] += 1
            if verbose:
                self.emit('# => r%d' % (where,))
        self.redvar_positions[arg] = where
        return where

    def redvar_position(self, arg):
        return self.redvar_positions[arg]

    def register_greenvar(self, arg, where=None, check=True, verbose=True):
        assert isinstance(arg, flowmodel.Variable) or not check
        if where is None:
            where = self.free_green[self.current_block]
            self.free_green[self.current_block] += 1
            if verbose:
                self.emit('# => g%d' % (where,))
        self.greenvar_positions[arg] = where
        return where

    def green_position(self, arg):
        if isinstance(arg, flowmodel.Variable):
            return self.greenvar_positions[arg]
        return ~self.const_position(arg)

    def const_position(self, const):
        if const in self.const_positions:
            return self.const_positions[const]
        const = self.RGenOp.constPrebuiltGlobal(const.value)
        result = len(self.constants)
        self.constants.append(const)
        self.const_positions[const] = result
        return result

    def type_position(self, TYPE):
        if TYPE in self.type_positions:
            return self.type_positions[TYPE]
        self.typekinds.append(self.RGenOp.kindToken(TYPE))
        self.redboxclasses.append(rvalue.ll_redboxcls(TYPE))
        result = len(self.type_positions)
        self.type_positions[TYPE] = result
        return result

    def structtypedesc_position(self, TYPE):
        if TYPE in self.structtypedesc_positions:
            return self.structtypedesc_positions[TYPE]
        self.structtypedescs.append(
            rcontainer.StructTypeDesc(self.RGenOp, TYPE))
        result = len(self.structtypedesc_positions)
        self.structtypedesc_positions[TYPE] = result
        return result

    def fielddesc_position(self, TYPE, fieldname):
        if (fieldname, TYPE) in self.fielddesc_positions:
            return self.fielddesc_positions[fieldname, TYPE]
        structtypedesc = rcontainer.StructTypeDesc(self.RGenOp, TYPE)
        fielddesc = structtypedesc.getfielddesc(fieldname)
        if fielddesc is None:
            self.fielddesc_positions[fieldname, TYPE] = -1
            return -1
        result = len(self.fielddescs)
        self.fielddescs.append(fielddesc)
        self.fielddesc_positions[fieldname, TYPE] = result
        return result

    def arrayfielddesc_position(self, TYPE):
        if TYPE in self.fielddesc_positions:
            return self.fielddesc_positions[TYPE]
        arrayfielddesc = rcontainer.ArrayFieldDesc(self.RGenOp, TYPE)
        result = len(self.arrayfielddescs)
        self.arrayfielddescs.append(arrayfielddesc)
        self.arrayfielddesc_positions[TYPE] = result
        return result

    def exceptioninstance_position(self, exc_class):
        if exc_class in self.exceptioninstance_positions:
            return self.exceptioninstance_positions[exc_class]
        bk = self.rtyper.annotator.bookkeeper
        exc_classdef = bk.getuniqueclassdef(exc_class)
        ll_exc = self.rtyper.exceptiondata.get_standard_ll_exc_instance(
            self.rtyper, exc_classdef)
        result = len(self.exceptioninstances)
        self.exceptioninstances.append(ll_exc)
        self.exceptioninstance_positions[exc_class] = result
        return result

    def oopspecdesc_position(self, fnobj, canraise):
        key = fnobj, canraise
        if key in self.oopspecdesc_positions:
            return self.oopspecdesc_positions[key]
        oopspecdesc = oop.OopSpecDesc(self.RGenOp, self.rtyper,
                                      fnobj, canraise)
        result = len(self.oopspecdescs)
        self.oopspecdescs.append(oopspecdesc)
        self.oopspecdesc_positions[key] = result
        return result

    def promotiondesc_position(self, TYPE):
        ERASED = self.RGenOp.erasedType(TYPE)
        if ERASED in self.promotiondesc_positions:
            return self.promotiondesc_positions[ERASED]
        promotiondesc = rtimeshift.PromotionDesc(ERASED, self.interpreter)
        result = len(self.promotiondescs)
        self.promotiondescs.append(promotiondesc)
        self.promotiondesc_positions[ERASED] = result
        return result

    def graph_position(self, graph):
        if graph in self.graph_positions:
            return self.graph_positions[graph]
        bytecode = self.get_jitcode(graph)
        index = len(self.called_bytecodes)
        self.called_bytecodes.append(bytecode)
        self.graph_positions[graph] = index
        return index

    def calldesc_position(self, FUNCTYPE, *voidargs):
        key = FUNCTYPE, voidargs
        if key in self.calldesc_positions:
            return self.calldesc_positions[key]
        result = len(self.calldescs)
        self.calldescs.append(
            CallDesc(self.RGenOp, self.rtyper, FUNCTYPE, voidargs))
        self.calldesc_positions[key] = result
        return result

    def metacalldesc_position(self, op):
        key = op
        if key in self.metacalldesc_positions:
            return self.metacalldesc_positions[key]
        result = len(self.metacalldescs)
        metadesc = op.args[1].value(self)
        ARGS = [arg.concretetype for arg in op.args[2:]]
        argiter = unrolling_iterable(ARGS)
        def func(interpreter, redargs):
            args = ()
            j = 0
            for ARG in argiter:
                if ARG == lltype.Void:
                    args += (None, )
                else:
                    box = redargs[j]
                    args += (box, )
                    j += 1
            return metadesc.metafunc(interpreter.jitstate, *args)
        self.metacalldescs.append(func)
        self.metacalldesc_positions[key] = result
        return result

    def indirectcalldesc_position(self, graph2code):
        key = graph2code.items()
        key.sort()
        key = tuple(key)
        if key in self.indirectcalldesc_positions:
            return self.indirectcalldesc_positions[key]
        callset = IndirectCallsetDesc(key, self)
        for i in range(len(key) + 1, 0, -1):
            subkey = key[:i]
            if subkey in self.indirectcalldesc_positions:
                result = self.indirectcalldesc_positions[subkey]
                self.indirectcalldescs[result] = callset
                break
        else:
            result = len(self.indirectcalldescs)
            self.indirectcalldescs.append(callset)
        for i in range(len(key) + 1, 0, -1):
            subkey = key[:i]
            self.indirectcalldesc_positions[subkey] = result
        return result

    def interiordesc(self, op, PTRTYPE, nb_offsets):
        path = []
        CONTAINER = PTRTYPE.TO
        indices_v = []
        for i in range(1, 1 + nb_offsets):
            varg = op.args[i]
            T = varg.concretetype
            if T is lltype.Void:
                fieldname = varg.value
                CONTAINER = getattr(CONTAINER, fieldname)
                path.append(fieldname)
            else:
                assert T is lltype.Signed
                CONTAINER = CONTAINER.OF
                path.append(None)    # placeholder for 'array index'
                indices_v.append(varg)
        if CONTAINER is lltype.Void:     # Void field
            return -1, None
        else:
            key = (PTRTYPE.TO, tuple(path))
            if key in self.interiordesc_positions:
                return self.interiordesc_positions[key]
            desc = rcontainer.InteriorDesc(self.RGenOp, PTRTYPE.TO, tuple(path))
            result = len(self.interiordescs)
            self.interiordescs.append(desc)
            return (result, indices_v)
        
    def emit(self, *stuff):
        assert stuff is not None
        for x in stuff:
            assert not isinstance(x, list)
            self.assembler.append(x)

    def sort_by_color(self, vars, by_color_of_vars=None):
        reds = []
        greens = []
        if by_color_of_vars is None:
            by_color_of_vars = vars
        for v, bcv in zip(vars, by_color_of_vars):
            if v.concretetype is lltype.Void:
                continue
            if self.hannotator.binding(bcv).is_green():
                greens.append(v)
            else:
                reds.append(v)
        return reds, greens

    # ____________________________________________________________
    # operation special cases

    def serialize_op_hint(self, op):
        hints = op.args[1].value
        arg = op.args[0]
        result = op.result
        assert len(hints) == 1
        hint = hints.keys()[0]
        handler = getattr(self, "handle_%s_hint" % (hint, ))
        return handler(op, arg, result)

    def handle_concrete_hint(self, op, arg, result):
        assert self.hannotator.binding(arg).is_green()
        assert self.hannotator.binding(result).is_green()
        self.register_greenvar(result, self.green_position(arg))

    def handle_variable_hint(self, op, arg, result):
        assert not self.hannotator.binding(result).is_green()
        if self.hannotator.binding(arg).is_green():
            resultindex = self.convert_to_red(arg)
            self.register_redvar(result, resultindex)
        else:
            self.register_redvar(result, self.redvar_position(arg))

    def handle_deepfreeze_hint(self, op, arg, result):
        if self.varcolor(result) == "red":
            self.register_redvar(result, self.redvar_position(arg))
        else:
            self.register_greenvar(result, self.green_position(arg))

    def handle_promote_hint(self, op, arg, result):
        if self.varcolor(arg) == "green":
            self.register_greenvar(result, self.green_position(arg))
            return
        self.emit("promote")
        self.emit(self.serialize_oparg("red", arg))
        self.emit(self.promotiondesc_position(arg.concretetype))
        self.register_greenvar(result)

    def handle_global_merge_point_hint(self, op, arg, result):
        return # the compute_merge_points function already cared

    def handle_reverse_split_queue_hint(self, op, arg, result):
        self.emit("reverse_split_queue")

    def handle_forget_hint(self, op, arg, result):
        # a hint for testing only
        assert self.varcolor(result) == "green"
        assert self.varcolor(arg) != "green"
        self.emit("revealconst")
        self.emit(self.serialize_oparg("red", arg))
        self.register_greenvar(result)

    def args_of_call(self, args, colored_as):
        result = []
        reds, greens = self.sort_by_color(args, colored_as)
        result = []
        for color, args in [("green", greens), ("red", reds)]:
            result.append(len(args))
            for v in args:
                result.append(self.serialize_oparg(color, v))
        return result

    def serialize_op_debug_assert(self, op):
        v = op.args[0]
        srcopname, srcargs = self.trace_back_bool_var(self.current_block, v)
        if srcopname in ('ptr_iszero', 'ptr_nonzero'):
            arg = self.serialize_oparg("red", srcargs[0])
            self.emit("learn_nonzeroness", arg, srcopname == "ptr_nonzero")

    def serialize_op_direct_call(self, op):
        kind, withexc = self.guess_call_kind(op)
        handler = getattr(self, "handle_%s_call" % (kind, ))
        print op, kind, withexc
        return handler(op, withexc)

    def serialize_op_ts_metacall(self, op):
        emitted_args = []
        for v in op.args[2:]:
            if v.concretetype != lltype.Void:
                emitted_args.append(self.serialize_oparg("red", v))
        metaindex = self.metacalldesc_position(op)
        self.emit("metacall", metaindex, len(emitted_args), *emitted_args)
        self.register_redvar(op.result)

    def serialize_op_indirect_call(self, op):
        kind, withexc = self.guess_call_kind(op)
        if kind == "green":
            return self.handle_green_call(op, withexc, exclude_last=True)
        targets = dict(self.graphs_from(op))
        fnptrindex = self.serialize_oparg("red", op.args[0])
        has_result = (self.varcolor(op.result) != "gray" and
                      op.result.concretetype != lltype.Void)
        if targets:
            self.emit("goto_if_constant", fnptrindex, tlabel(("direct call", op)))

        emitted_args = []
        for v in op.args[1:-1]:
            if v.concretetype == lltype.Void:
                continue
            emitted_args.append(self.serialize_oparg("red", v))
        self.emit("red_residual_call")
        calldescindex = self.calldesc_position(op.args[0].concretetype)
        self.emit(fnptrindex, calldescindex, withexc, has_result)
        self.emit(len(emitted_args), *emitted_args)
        self.emit(self.promotiondesc_position(lltype.Signed))

        if has_result:
            self.register_redvar(op.result)

        if targets:
            self.emit("goto", tlabel(("after indirect call", op)))

            self.emit(label(("direct call", op)))
            args = targets.values()[0].getargs()
            emitted_args = self.args_of_call(op.args[1:-1], args)
            self.emit("indirect_call_const")
            self.emit(*emitted_args)
            setdescindex = self.indirectcalldesc_position(targets)
            self.emit(fnptrindex, setdescindex)
            if kind == "yellow":
                self.emit("yellow_retrieve_result_as_red")
                self.emit(self.type_position(op.result.concretetype))
            elif kind in ("gray", "red"):
                pass
            else:
                assert 0, "unknown call kind %s" % (kind, )

            self.emit(label(("after indirect call", op)))


    def handle_oopspec_call(self, op, withexc):
        from pypy.jit.timeshifter.oop import Index
        fnobj = op.args[0].value._obj
        oopspecdescindex = self.oopspecdesc_position(fnobj, withexc)
        oopspecdesc = self.oopspecdescs[oopspecdescindex]
        opargs = op.args[1:]
        args_v = []
        args = []
        for obj in oopspecdesc.argtuple:
            if isinstance(obj, Index):
                v = opargs[obj.n]
            else:
                v = flowmodel.Constant(obj, lltype.typeOf(obj))
            args_v.append(v)
            args.append(self.serialize_oparg("red", v))

        if oopspecdesc.is_method:
            hs_self = self.hannotator.binding(
                opargs[oopspecdesc.argtuple[0].n])
            deepfrozen = hs_self.deepfrozen
        else:
            deepfrozen = False

        hasresult = op.result.concretetype != lltype.Void
        self.emit("red_oopspec_call%s_%s" % ("_noresult" * (not hasresult),
                                             len(args)))
        self.emit(oopspecdescindex)
        self.emit(deepfrozen)
        self.emit(*args)
        if hasresult:
            self.register_redvar(op.result)

        if withexc:
            self.emit("goto_if_oopcall_was_virtual", tlabel(("oop_call", op)))
            self.emit("after_oop_residual_call")
            self.emit(self.promotiondesc_position(lltype.Signed))
            self.emit(label(("oop_call", op)))

    def handle_green_call(self, op, withexc, exclude_last=False):
        if exclude_last:
            args = op.args[1:-1]
        else:
            args = op.args[1:]
        voidargs = [const.value for const in args
                        if const.concretetype == lltype.Void]
        fnptr = op.args[0]
        pos = self.calldesc_position(fnptr.concretetype, *voidargs)
        func = self.serialize_oparg("green", fnptr)
        emitted_args = []
        for v in op.args[1:]:
            if v.concretetype != lltype.Void:
                emitted_args.append(self.serialize_oparg("green", v))
        self.emit("green_call")
        self.emit(func, pos)
        self.emit(len(emitted_args))
        self.emit(*emitted_args)
        self.register_greenvar(op.result)

    def handle_residual_call(self, op, withexc):
        fnptr = op.args[0]
        pos = self.calldesc_position(lltype.typeOf(fnptr.value))
        has_result = (self.varcolor(op.result) != "gray" and
                      op.result.concretetype != lltype.Void)
        func = self.serialize_oparg("red", fnptr)
        emitted_args = []
        for v in op.args[1:]:
            if v.concretetype == lltype.Void:
                continue
            emitted_args.append(self.serialize_oparg("red", v))
        self.emit("red_residual_call")
        self.emit(func, pos, withexc, has_result, len(emitted_args))
        self.emit(*emitted_args)
        self.emit(self.promotiondesc_position(lltype.Signed))
        if has_result:
            self.register_redvar(op.result)

    def handle_rpyexc_raise_call(self, op, withexc):
        emitted_args = []
        for v in op.args[1:]:
            emitted_args.append(self.serialize_oparg("red", v))
        self.emit("setexception", *emitted_args)

    def handle_red_call(self, op, withexc, kind="red"):
        targets = dict(self.graphs_from(op))
        assert len(targets) == 1
        targetgraph, = targets.values()
        graphindex = self.graph_position(targetgraph)
        bytecode = self.all_graphs[targetgraph]
        args = targetgraph.getargs()
        emitted_args = self.args_of_call(op.args[1:], args)

        if bytecode.is_portal:
            self.emit("portal_call", *emitted_args)
        else:
            self.emit("red_direct_call")
            self.emit(*emitted_args)
            self.emit(graphindex)

        if kind == "red":
            self.register_redvar(op.result)
    
    def handle_gray_call(self, op, withexc):
        return self.handle_red_call(op, withexc, "gray")

    def handle_yellow_call(self, op, withexc):
        targets = dict(self.graphs_from(op))
        assert len(targets) == 1
        targetgraph, = targets.values()
        graphindex = self.graph_position(targetgraph)
        args = targetgraph.getargs()
        emitted_args = self.args_of_call(op.args[1:], args)
        self.emit("yellow_direct_call")
        self.emit(*emitted_args)
        self.emit(graphindex)
        self.emit("yellow_retrieve_result")
        self.register_greenvar(op.result)

    def handle_vable_call(self, op, withexc):
        assert op.opname == 'direct_call'
        oopspec = op.args[0].value._obj._callable.oopspec
        name, _ = oopspec.split('(')
        kind, name = name.split('_', 1)

        if kind == 'vable.get':
            opname = 'getfield'
        else:
            assert kind == 'vable.set'
            opname = 'setfield'
        args = op.args[1:]
        args.insert(1, flowmodel.Constant(name, lltype.Void))
        newop = flowmodel.SpaceOperation(opname, args, op.result)
        self.serialize_op(newop)

    def serialize_op_malloc(self, op):
        index = self.structtypedesc_position(op.args[0].value)
        self.emit("red_malloc", index)
        self.register_redvar(op.result)

    def serialize_op_malloc_varsize(self, op):
        PTRTYPE = op.result.concretetype
        TYPE = PTRTYPE.TO
        v_size = op.args[2]
        sizeindex = self.serialize_oparg("red", v_size)
        if isinstance(TYPE, lltype.Struct):
            index = self.structtypedesc_position(op.args[0].value)
            self.emit("red_malloc_varsize_struct")
        else:
            index = self.arrayfielddesc_position(TYPE)
            self.emit("red_malloc_varsize_array")
        self.emit(index, sizeindex)
        self.register_redvar(op.result)

    def serialize_op_zero_gc_pointers_inside(self, op):
        pass # XXX is that right?

    def serialize_op_resume_point(self, op):
        pass

    def serialize_op_keepalive(self, op):
        pass

    def serialize_op_cast_pointer(self, op):
        color = self.varcolor(op.result)
        assert color == self.varcolor(op.args[0])
        if color == "green":
            self.register_greenvar(op.result, self.green_position(op.args[0]))
        else:
            self.register_redvar(op.result, self.redvar_position(op.args[0]))

    def serialize_op_keepalive(self, op):
        pass

    def serialize_op_getfield(self, op):
        color = self.opcolor(op)
        args = op.args
        if args[0] == self.exceptiondesc.cexcdata:
            # reading one of the exception boxes (exc_type or exc_value)
            fieldname = args[1].value
            if fieldname == 'exc_type':
                self.emit("read_exctype")
            elif fieldname == 'exc_value':
                self.emit("read_excvalue")
            else:
                raise Exception("getfield(exc_data, %r)" % (fieldname,))
            self.register_redvar(op.result)
            return

        # virtualizable access read
        PTRTYPE = args[0].concretetype
        if PTRTYPE.TO._hints.get('virtualizable', False):
            assert op.args[1].value != 'vable_access'

        # non virtual case                
        index = self.serialize_oparg("red", args[0])
        fieldname = args[1].value
        s_struct = self.hannotator.binding(args[0])
        deepfrozen = s_struct.deepfrozen
        
        fielddescindex = self.fielddesc_position(PTRTYPE.TO, fieldname)
        if fielddescindex == -1:   # Void field
            return
        self.emit("%s_getfield" % (color, ), index, fielddescindex, deepfrozen)
        if color == "red":
            self.register_redvar(op.result)
        else:
            self.register_greenvar(op.result)

    def serialize_op_setfield(self, op):
        args = op.args
        PTRTYPE = args[0].concretetype
        VALUETYPE = args[2].concretetype
        if VALUETYPE is lltype.Void:
            return
        if args[0] == self.exceptiondesc.cexcdata:
            # reading one of the exception boxes (exc_type or exc_value)
            fieldname = args[1].value
            val = self.serialize_oparg("red", args[2])
            if fieldname == 'exc_type':
                self.emit("write_exctype", val)
            elif fieldname == 'exc_value':
                self.emit("write_excvalue", val)
            else:
                raise Exception("getfield(exc_data, %r)" % (fieldname,))
            return
        # non virtual case                
        destboxindex = self.serialize_oparg("red", args[0])
        valboxindex = self.serialize_oparg("red", args[2])
        fieldname = args[1].value
        fielddescindex = self.fielddesc_position(PTRTYPE.TO, fieldname)
        if fielddescindex == -1:   # Void field
            return
        self.emit("red_setfield", destboxindex, fielddescindex, valboxindex)

    def serialize_op_getarrayitem(self, op):
        arrayvar, indexvar = op.args
        PTRTYPE = arrayvar.concretetype
        if PTRTYPE.TO.OF is lltype.Void:
            return
        deepfrozen = self.hannotator.binding(arrayvar).deepfrozen
        fielddescindex = self.arrayfielddesc_position(PTRTYPE.TO)
        arrayindex = self.serialize_oparg("red", arrayvar)
        index = self.serialize_oparg("red", indexvar)
        self.emit("red_getarrayitem", arrayindex, fielddescindex, index,
                  deepfrozen)
        self.register_redvar(op.result)

    def serialize_op_setarrayitem(self, op):
        args = op.args
        PTRTYPE = args[0].concretetype
        VALUETYPE = PTRTYPE.TO.OF
        if VALUETYPE is lltype.Void:
            return
        destboxindex = self.serialize_oparg("red", args[0])
        indexboxindex = self.serialize_oparg("red", args[1])
        valboxindex = self.serialize_oparg("red", args[2])
        fielddescindex = self.arrayfielddesc_position(PTRTYPE.TO)
        if fielddescindex == -1:   # Void field
            return
        self.emit("red_setarrayitem", destboxindex, fielddescindex,
                  indexboxindex, valboxindex)

    def serialize_op_getarraysize(self, op):
        color = self.opcolor(op)
        arrayvar, = op.args
        PTRTYPE = arrayvar.concretetype
        if PTRTYPE.TO.OF is lltype.Void:
            return
        fielddescindex = self.arrayfielddesc_position(PTRTYPE.TO)
        arrayindex = self.serialize_oparg("red", arrayvar)
        self.emit("%s_getarraysize" % (color, ), arrayindex, fielddescindex)
        if color == "red":
            self.register_redvar(op.result)
        else:
            self.register_greenvar(op.result)

    def serialize_op_getinteriorfield(self, op):
        color = self.opcolor(op)
        structvar = op.args[0]
        PTRTYPE = structvar.concretetype
        # no virtualizable access read here
        assert not PTRTYPE.TO._hints.get('virtualizable', False)

        # non virtual case
        interiordescindex, indices_v = self.interiordesc(
                op, PTRTYPE, len(op.args) - 1)
        if interiordescindex == -1:    # Void field
            return None
        structindex = self.serialize_oparg("red", structvar)
        deepfrozen = self.hannotator.binding(structvar).deepfrozen
        indexes = []
        for arg in indices_v:
            indexes.append(self.serialize_oparg("red", arg))
        self.emit("%s_getinteriorfield" % color, structindex,
                  interiordescindex, deepfrozen)
        self.emit(len(indexes))
        self.emit(*indexes)
        if color == "red":
            self.register_redvar(op.result)
        else:
            self.register_greenvar(op.result)

    def serialize_op_setinteriorfield(self, op):
        structvar = op.args[0]
        valuevar = op.args[-1]
        PTRTYPE = structvar.concretetype
        # non virtual case
        interiordescindex, indices_v = self.interiordesc(
                op, PTRTYPE, len(op.args) - 2)
        structindex = self.serialize_oparg("red", structvar)
        indexes = []
        for arg in indices_v:
            indexes.append(self.serialize_oparg("red", arg))
        valueindex = self.serialize_oparg("red", valuevar)
        self.emit("red_setinteriorfield", structindex, interiordescindex)
        self.emit(len(indexes))
        self.emit(*indexes)
        self.emit(valueindex)

    def serialize_op_getinteriorarraysize(self, op):
        structvar = op.args[0]
        PTRTYPE = structvar.concretetype
        color = self.opcolor(op)
        # non virtual case
        interiordescindex, indices_v = self.interiordesc(
                op, PTRTYPE, len(op.args) - 1)
        assert interiordescindex != -1
        structindex = self.serialize_oparg("red", structvar)
        indexes = []
        for arg in indices_v:
            indexes.append(self.serialize_oparg("red", arg))
        self.emit("%s_getinteriorarraysize" % color, structindex,
                  interiordescindex)
        self.emit(len(indexes))
        self.emit(*indexes)
        if color == "red":
            self.register_redvar(op.result)
        else:
            self.register_greenvar(op.result)

    def serialize_op_is_early_constant(self, op):
        consttrue = flowmodel.Constant(True, lltype.Bool)
        trueindex = self.serialize_oparg("green", consttrue)
        if self.varcolor(op.args[0]) == "green":
            self.register_greenvar(op.result, trueindex)
        else:
            constfalse = flowmodel.Constant(False, lltype.Bool)
            falseindex = self.serialize_oparg("green", constfalse)
            argindex = self.serialize_oparg("red", op.args[0])
            self.emit("is_constant")
            self.emit(argindex, trueindex, falseindex)
            self.register_greenvar(op.result)


    # call handling

    def graphs_from(self, spaceop):
        if spaceop.opname == 'direct_call':
            c_func = spaceop.args[0]
            fnobj = c_func.value._obj
            graphs = [fnobj.graph]
            args_v = spaceop.args[1:]
        elif spaceop.opname == 'indirect_call':
            graphs = spaceop.args[-1].value
            if graphs is None:
                return       # cannot follow at all
            args_v = spaceop.args[1:-1]
        else:
            raise AssertionError(spaceop.opname)
        # if the graph - or all the called graphs - are marked as "don't
        # follow", directly return None as a special case.  (This is only
        # an optimization for the indirect_call case.)
        for graph in graphs:
            if self.hannotator.policy.look_inside_graph(graph):
                break
        else:
            return
        for graph in graphs:
            tsgraph = self.specialized_graph_of(graph, args_v, spaceop.result)
            yield graph, tsgraph

    def trace_back_bool_var(self, block, v):
        """Return the (opname, arguments) that created the exitswitch of
        the block.  The opname is None if not found.
        """
        inverted = False
        for i in range(len(block.operations)-1, -1, -1):
            op = block.operations[i]
            if op.result is v:
                if op.opname == 'bool_not':
                    inverted = not inverted
                    [v] = op.args
                elif op.opname == 'same_as':
                    [v] = op.args
                else:
                    opname = op.opname
                    opargs = op.args
                    if inverted:
                        opname = {'ptr_nonzero': 'ptr_iszero',
                                  'ptr_iszero' : 'ptr_nonzero'}.get(opname)
                    return opname, opargs    # found
        # not found, comes from earlier block - give up
        return None, None

    def guess_call_kind(self, spaceop):
        if spaceop.opname == 'direct_call':
            c_func = spaceop.args[0]
            fnobj = c_func.value._obj
            if hasattr(fnobj, 'jitcallkind'):
                return fnobj.jitcallkind, None
            if (hasattr(fnobj._callable, 'oopspec') and
                self.hannotator.policy.oopspec):
                if fnobj._callable.oopspec.startswith('vable.'):
                    return 'vable', None
                hs_result = self.hannotator.binding(spaceop.result)
                if (hs_result.is_green() and
                    hs_result.concretetype is not lltype.Void):
                    return 'green', self.can_raise(spaceop)
                return 'oopspec', self.can_raise(spaceop)
        if self.hannotator.bookkeeper.is_green_call(spaceop):
            return 'green', None
        withexc = self.can_raise(spaceop)
        colors = {}
        for graph, tsgraph in self.graphs_from(spaceop):
            color = self.graph_calling_color(tsgraph)
            colors[color] = tsgraph
        if not colors: # cannot follow this call
            return 'residual', withexc
        assert len(colors) == 1, colors   # buggy normalization?
        return color, withexc

    def specialized_graph_of(self, graph, args_v, v_result):
        bk = self.hannotator.bookkeeper
        args_hs = [self.hannotator.binding(v) for v in args_v]
        hs_result = self.hannotator.binding(v_result)
        if isinstance(hs_result, hintmodel.SomeLLAbstractConstant):
            fixed = hs_result.is_fixed()
        else:
            fixed = False
        specialization_key = bk.specialization_key(fixed, args_hs)
        special_graph = bk.get_graph_by_key(graph, specialization_key)
        return special_graph

    def graph_calling_color(self, graph):
        hs_res = self.hannotator.binding(graph.getreturnvar())
        if originalconcretetype(hs_res) is lltype.Void:
            c = 'gray'
        elif hs_res.is_green():
            c = 'yellow'
        else:
            c = 'red'
        return c


class GraphTransformer(object):
    def __init__(self, hannotator):
        self.hannotator = hannotator

    def transform_graph(self, graph):
        from pypy.translator.backendopt.constfold import constant_fold_graph
        self.graph = graph
        remove_same_as(graph)
        # to get rid of the usages of the we_are_jitted constant
        # (turned to '1' by the hintannotator)
        # XXX is this the best way to deal with it?
        constant_fold_graph(graph)
        self.insert_splits()

    def insert_splits(self):
        hannotator = self.hannotator
        for block in list(self.graph.iterblocks()):
            if block.exitswitch is not None:
                assert isinstance(block.exitswitch, flowmodel.Variable)
                hs_switch = hannotator.binding(block.exitswitch)
                if not hs_switch.is_green():
                    if block.exitswitch.concretetype is not lltype.Bool:
                        self.insert_switch_handling(block)

    def insert_switch_handling(self, block):
        v_redswitch = block.exitswitch
        T = v_redswitch.concretetype
        range_start = -py.std.sys.maxint-1
        range_stop  = py.std.sys.maxint+1
        if T is not lltype.Signed:
            if T is lltype.Char:
                opcast = 'cast_char_to_int'
                range_start = 0
                range_stop = 256
            elif T is lltype.UniChar:
                opcast = 'cast_unichar_to_int'
                range_start = 0
            elif T is lltype.Unsigned:
                opcast = 'cast_uint_to_int'
            else:
                raise AssertionError(T)
            v_redswitch = self.genop(block, opcast, [v_redswitch],
                                     resulttype=lltype.Signed, red=True)
            block.exitswitch = v_redswitch
        # for now, we always turn the switch back into a chain of tests
        # that perform a binary search
        blockset = {block: True}   # reachable from outside
        cases = {}
        defaultlink = None
        for link in block.exits:
            if link.exitcase == 'default':
                defaultlink = link
                blockset[link.target] = False   # not reachable from outside
            else:
                assert lltype.typeOf(link.exitcase) == T
                intval = lltype.cast_primitive(lltype.Signed, link.exitcase)
                cases[intval] = link
                link.exitcase = None
                link.llexitcase = None
        self.insert_integer_search(block, cases, defaultlink, blockset,
                                   range_start, range_stop)
        SSA_to_SSI(blockset, self.hannotator)

    def insert_integer_search(self, block, cases, defaultlink, blockset,
                              range_start, range_stop):
        # fix the exit of the 'block' to check for the given remaining
        # 'cases', knowing that if we get there then the value must
        # be contained in range(range_start, range_stop).
        if not cases:
            assert defaultlink is not None
            block.exitswitch = None
            block.recloseblock(flowmodel.Link(defaultlink.args, defaultlink.target))
        elif len(cases) == 1 and (defaultlink is None or
                                  range_start == range_stop-1):
            block.exitswitch = None
            block.recloseblock(cases.values()[0])
        else:
            intvalues = cases.keys()
            intvalues.sort()
            if len(intvalues) <= 3:
                # not much point in being clever with no more than 3 cases
                intval = intvalues[-1]
                remainingcases = cases.copy()
                link = remainingcases.pop(intval)
                c_intval = flowmodel.Constant(intval, lltype.Signed)
                v = self.genop(block, 'int_eq', [block.exitswitch, c_intval],
                               resulttype=lltype.Bool, red=True)
                link.exitcase = True
                link.llexitcase = True
                falseblock = flowmodel.Block([])
                falseblock.exitswitch = block.exitswitch
                blockset[falseblock] = False
                falselink = flowmodel.Link([], falseblock)
                falselink.exitcase = False
                falselink.llexitcase = False
                block.exitswitch = v
                block.recloseblock(falselink, link)
                if defaultlink is None or intval == range_stop-1:
                    range_stop = intval
                self.insert_integer_search(falseblock, remainingcases,
                                           defaultlink, blockset,
                                           range_start, range_stop)
            else:
                intval = intvalues[len(intvalues) // 2]
                c_intval = flowmodel.Constant(intval, lltype.Signed)
                v = self.genop(block, 'int_ge', [block.exitswitch, c_intval],
                               resulttype=lltype.Bool, red=True)
                falseblock = flowmodel.Block([])
                falseblock.exitswitch = block.exitswitch
                trueblock  = flowmodel.Block([])
                trueblock.exitswitch = block.exitswitch
                blockset[falseblock] = False
                blockset[trueblock]  = False
                falselink = flowmodel.Link([], falseblock)
                falselink.exitcase = False
                falselink.llexitcase = False
                truelink = flowmodel.Link([], trueblock)
                truelink.exitcase = True
                truelink.llexitcase = True
                block.exitswitch = v
                block.recloseblock(falselink, truelink)
                falsecases = {}
                truecases = {}
                for intval1, link1 in cases.items():
                    if intval1 < intval:
                        falsecases[intval1] = link1
                    else:
                        truecases[intval1] = link1
                self.insert_integer_search(falseblock, falsecases,
                                           defaultlink, blockset,
                                           range_start, intval)
                self.insert_integer_search(trueblock, truecases,
                                           defaultlink, blockset,
                                           intval, range_stop)

    def genop(self, block, opname, args, resulttype=None, result_like=None, red=False):
        # 'result_like' can be a template variable whose hintannotation is
        # copied
        if resulttype is not None:
            v_res = varoftype(resulttype)
            if red:
                hs = hintmodel.SomeLLAbstractVariable(resulttype)
            else:
                hs = hintmodel.SomeLLAbstractConstant(resulttype, {})
            self.hannotator.setbinding(v_res, hs)
        elif result_like is not None:
            v_res = copyvar(self.hannotator, result_like)
        else:
            v_res = self.new_void_var()

        spaceop = flowmodel.SpaceOperation(opname, args, v_res)
        if isinstance(block, list):
            block.append(spaceop)
        else:
            block.operations.append(spaceop)
        return v_res

class label(object):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "label(%r)" % (self.name, )

class tlabel(object):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "tlabel(%r)" % (self.name, )

def assemble_labelpos(labelpos, interpreter, *args):
    result = []
    def emit_2byte(index):
        assert -32768 <= index < 32768
        result.append(chr((index >> 8) & 0xff))
        result.append(chr(index & 0xff))
    for arg in args:
        if isinstance(arg, str):
            if arg.startswith('#'):     # skip comments
                continue
            opcode = interpreter.find_opcode(arg)
            assert opcode >= 0, "unknown opcode %s" % (arg, )
            emit_2byte(opcode)
        elif isinstance(arg, bool):
            result.append(chr(int(arg)))
        elif isinstance(arg, int):
            emit_2byte(arg)
        elif isinstance(arg, label):
            labelpos[arg.name] = len(result)
        elif isinstance(arg, tlabel):
            result.extend((arg, None, None, None))
        else:
            assert "don't know how to emit %r" % (arg, )
    for i in range(len(result)):
        b = result[i]
        if isinstance(b, tlabel):
            for j in range(1, 4):
                assert result[i + j] is None
            index = labelpos[b.name]
            result[i + 0] = chr((index >> 24) & 0xff)
            result[i + 1] = chr((index >> 16) & 0xff)
            result[i + 2] = chr((index >>  8) & 0xff)
            result[i + 3] = chr(index & 0xff)
    return "".join(result)

def assemble(interpreter, *args):
    return assemble_labelpos({}, interpreter, *args)
